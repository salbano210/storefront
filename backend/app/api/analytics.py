"""Analytics: conversation volume by intent, and how conversations were disposed.

Reads the denormalised ``intent`` / ``disposition`` / ``last_outcome`` columns on
the conversation rather than replaying the event log, so the whole dashboard is a
single query plus in-process bucketing. Bucketing happens in Python on purpose:
SQLite and Postgres disagree about date truncation, and at demo volume the cost
is irrelevant.

Every session is included. There is no "my session" view: a dashboard that hides
most of its data is not a dashboard, and the logs page works the same way.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Conversation
from ..orchestrator import DISPOSITIONS
from ..services.seeder import LIVE_SOURCE, SAMPLE_SOURCE

router = APIRouter(prefix="/api", tags=["analytics"])

INTERVALS = ("day", "week", "month")

# The approval rate is plotted as a trailing window rather than per bucket. A daily
# rate over one or two decisions is 0% or 100%, which reads as noise; three weeks of
# them reads as a trend. The window is expressed in days so the meaning is the same
# at every interval, and rounded to whole buckets.
RATE_WINDOW_DAYS = 21
# Below this many decisions a rate is not plotted at all -- the point would be a
# single approval dressed up as a percentage. Eight is where a rate stops swinging
# by more than a few points per decision.
RATE_MIN_DECIDED = 8
BUCKET_DAYS = {"day": 1, "week": 7, "month": 30}

# The intent vocabulary, with the names the dashboard shows.
INTENT_LABELS = {
    "gift_request": "Gift request",
    "general_inquiry": "Other",
    "unknown": "Unclassified",
}
INTENT_ORDER = ("gift_request", "general_inquiry")

DISPOSITION_LABELS = {
    "gift_request_approved": "Gift request approved",
    "gift_request_denied": "Gift request denied",
    "other": "Other",
    "escalated": "Escalated to a human",
}

# Why a conversation landed in "other" -- the raw outcome behind the bucket.
OUTCOME_LABELS = {
    "gift_issued": "Gift issued",
    "gift_duplicate": "Gift already issued",
    "gift_declined": "Gift declined",
    "gift_suggested": "Product suggested",
    "details_required": "Awaiting details",
    "inquiry": "Answered a question",
    "escalated": "Escalated to a human",
    "none": "No outcome yet",
}


def _as_utc(moment: datetime) -> datetime:
    """Normalise a stored timestamp (SQLite hands back naive UTC)."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _bucket_key(moment: datetime, interval: str) -> str:
    if interval == "month":
        return moment.strftime("%Y-%m")
    if interval == "week":
        return (moment.date() - timedelta(days=moment.weekday())).isoformat()
    return moment.strftime("%Y-%m-%d")


def _bucket_starts(start: date, end: date, interval: str) -> list[str]:
    """Every bucket in the range, so the chart keeps a continuous time axis."""
    if interval == "month":
        keys: list[str] = []
        cursor = date(start.year, start.month, 1)
        while cursor <= end:
            keys.append(cursor.strftime("%Y-%m"))
            cursor = date(cursor.year + (cursor.month == 12), (cursor.month % 12) + 1, 1)
        return keys

    step = timedelta(days=7 if interval == "week" else 1)
    cursor = start - timedelta(days=start.weekday()) if interval == "week" else start
    keys = []
    while cursor <= end:
        keys.append(cursor.isoformat())
        cursor += step
    return keys


def _bucket_label(bucket: str, interval: str) -> str:
    if interval == "month":
        moment = datetime.strptime(bucket, "%Y-%m")
        return f"{moment.strftime('%b')} {moment.year}"
    moment = datetime.strptime(bucket, "%Y-%m-%d")
    stamp = f"{moment.day} {moment.strftime('%b')}"
    return f"wk {stamp}" if interval == "week" else stamp


def _tally(counter: Counter, order: tuple[str, ...], key: str, total: int) -> list[dict]:
    labels = INTENT_LABELS if key == "intent" else DISPOSITION_LABELS
    return [
        {
            key: item,
            "label": labels.get(item) or item.replace("_", " ").capitalize(),
            "count": counter.get(item, 0),
            "rate": round(counter.get(item, 0) / total, 4) if total else 0.0,
        }
        for item in order
    ]


@router.get("/analytics")
async def analytics(
    db: AsyncSession = Depends(get_db),
    days: int = Query(30, ge=1, le=365),
    interval: str = Query("day", pattern="^(day|week|month)$"),
) -> dict:
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)

    # A conversation is dated by when it started, so chatting further never moves
    # it into a different day.
    rows = list(await db.scalars(select(Conversation)))
    in_range = [row for row in rows if start <= _as_utc(row.created_at).date() <= today]

    intents = Counter(row.intent or "unknown" for row in in_range)
    dispositions = Counter(row.disposition or "other" for row in in_range)
    outcomes = Counter(row.last_outcome or "none" for row in in_range)

    intent_order = INTENT_ORDER + tuple(sorted(set(intents) - set(INTENT_ORDER)))
    per_bucket_intents: dict[str, Counter] = defaultdict(Counter)
    per_bucket_dispositions: dict[str, Counter] = defaultdict(Counter)
    for row in in_range:
        bucket = _bucket_key(_as_utc(row.created_at), interval)
        per_bucket_intents[bucket][row.intent or "unknown"] += 1
        per_bucket_dispositions[bucket][row.disposition or "other"] += 1

    # The trailing window reaches back before the range, so the first points of the
    # line are not computed from a stub of a window.
    window_buckets = max(1, round(RATE_WINDOW_DAYS / BUCKET_DAYS[interval]))
    all_buckets = _bucket_starts(start - timedelta(days=RATE_WINDOW_DAYS * 2), today, interval)
    window_counts: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        moment = _as_utc(row.created_at)
        if moment.date() < start - timedelta(days=RATE_WINDOW_DAYS * 2):
            continue
        window_counts[_bucket_key(moment, interval)][row.disposition or "other"] += 1

    def rate_window(bucket: str) -> dict:
        """Decisions in the trailing window, and the rate they imply."""
        index = all_buckets.index(bucket)
        span = all_buckets[max(0, index - window_buckets + 1) : index + 1]
        approved = sum(window_counts[item]["gift_request_approved"] for item in span)
        denied = sum(window_counts[item]["gift_request_denied"] for item in span)
        decided = approved + denied
        return {
            "window_buckets": len(span),
            "approved": approved,
            "denied": denied,
            "decided": decided,
            "value": round(approved / decided, 4)
            if decided >= RATE_MIN_DECIDED
            else None,
        }

    series = []
    for bucket in _bucket_starts(start, today, interval):
        bucket_intents = per_bucket_intents[bucket]
        bucket_dispositions = per_bucket_dispositions[bucket]
        series.append(
            {
                "bucket": bucket,
                "label": _bucket_label(bucket, interval),
                "total": sum(bucket_intents.values()),
                "counts": {
                    intent: bucket_intents.get(intent, 0) for intent in intent_order
                },
                # The stacked chart's segments: this bucket only.
                "dispositions": {
                    item: bucket_dispositions.get(item, 0) for item in DISPOSITIONS
                },
                # The line's point: the trailing window ending here.
                "rate": rate_window(bucket),
            }
        )

    total = len(in_range)
    seeded = sum(1 for row in in_range if (row.source or LIVE_SOURCE) == SAMPLE_SOURCE)
    approved_total = dispositions.get("gift_request_approved", 0)
    denied_total = dispositions.get("gift_request_denied", 0)
    decided_total = approved_total + denied_total
    return {
        "range": {
            "from": start.isoformat(),
            "to": today.isoformat(),
            "days": days,
            "interval": interval,
        },
        "rate_window": {
            "days": RATE_WINDOW_DAYS,
            "buckets": window_buckets,
            "min_decided": RATE_MIN_DECIDED,
        },
        "totals": {
            "conversations": total,
            "live": total - seeded,
            "seeded": seeded,
            "decided": decided_total,
            "approval_rate": round(approved_total / decided_total, 4)
            if decided_total
            else None,
        },
        "by_intent": _tally(intents, intent_order, "intent", total),
        "by_disposition": _tally(dispositions, DISPOSITIONS, "disposition", total),
        "by_outcome": sorted(
            (
                {
                    "outcome": key,
                    "label": OUTCOME_LABELS.get(key, key),
                    "count": value,
                }
                for key, value in outcomes.items()
            ),
            key=lambda item: (-item["count"], item["outcome"]),
        ),
        "series": series,
    }
