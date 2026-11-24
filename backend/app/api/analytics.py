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
# A quiet window still produces a point drawn from three decisions, which is how a
# line ends up claiming a 33% spike that never happened. So the window widens
# backwards until this many decisions are behind the point, up to RATE_MAX_WINDOW_DAYS.
# Below that ceiling the point is not plotted at all.
RATE_TARGET_DECIDED = 20
RATE_MAX_WINDOW_DAYS = 60
BUCKET_DAYS = {"day": 1, "week": 7, "month": 30}

# The intent vocabulary, with the names the dashboard shows.
INTENT_LABELS = {
    "gift_request": "Gift request",
    "out_of_scope": "Out of scope",
    "unknown": "Unclassified",
}
INTENT_ORDER = ("gift_request", "out_of_scope")

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
    max_window_buckets = max(
        window_buckets, round(RATE_MAX_WINDOW_DAYS / BUCKET_DAYS[interval])
    )
    lookback = start - timedelta(days=RATE_MAX_WINDOW_DAYS)
    all_buckets = _bucket_starts(lookback, today, interval)
    window_counts: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        moment = _as_utc(row.created_at)
        if moment.date() < lookback:
            continue
        window_counts[_bucket_key(moment, interval)][row.disposition or "other"] += 1

    def window_totals(index: int, width: int) -> tuple[list[str], int, int]:
        span = all_buckets[max(0, index - width + 1) : index + 1]
        approved = sum(window_counts[item]["gift_request_approved"] for item in span)
        denied = sum(window_counts[item]["gift_request_denied"] for item in span)
        return span, approved, denied

    def rate_window(bucket: str) -> dict:
        """Decisions in the trailing window, and the rate they imply.

        The window opens at its nominal width and widens backwards until enough
        decisions are behind the point. A quiet fortnight otherwise yields a rate
        computed from three decisions, which swings the line by thirty points.
        """
        index = all_buckets.index(bucket)
        width = window_buckets
        span, approved, denied = window_totals(index, width)
        while approved + denied < RATE_TARGET_DECIDED and width < max_window_buckets:
            width += 1
            span, approved, denied = window_totals(index, width)
        decided = approved + denied
        return {
            # The span it actually used, which is the nominal width unless the data
            # was thin enough to force it wider.
            "window_days": len(span) * BUCKET_DAYS[interval],
            "approved": approved,
            "denied": denied,
            "decided": decided,
            # None where even the widest window cannot reach the target: there is no
            # rate worth drawing, so the line breaks rather than inventing a cliff.
            "value": round(approved / decided, 4)
            if decided >= RATE_TARGET_DECIDED
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
            "target_decided": RATE_TARGET_DECIDED,
            "max_days": RATE_MAX_WINDOW_DAYS,
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
