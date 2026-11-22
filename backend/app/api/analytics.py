"""Analytics: conversation volume by intent, and how conversations were disposed.

Reads the denormalised ``intent`` / ``disposition`` / ``last_outcome`` columns on
the conversation rather than replaying the event log, so the whole dashboard is a
single query plus in-process bucketing. Bucketing happens in Python on purpose:
SQLite and Postgres disagree about date truncation, and at demo volume the cost
is irrelevant.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Conversation
from ..orchestrator import DISPOSITIONS
from .deps import is_admin, session_id

router = APIRouter(prefix="/api", tags=["analytics"])

INTERVALS = ("day", "week", "month")

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
}

# Why a conversation landed in "other" -- the raw outcome behind the bucket.
OUTCOME_LABELS = {
    "gift_issued": "Gift issued",
    "gift_duplicate": "Gift already issued",
    "gift_declined": "Gift declined",
    "gift_suggested": "Product suggested",
    "details_required": "Awaiting details",
    "inquiry": "Answered a question",
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
            "label": labels.get(item, item),
            "count": counter.get(item, 0),
            "rate": round(counter.get(item, 0) / total, 4) if total else 0.0,
        }
        for item in order
    ]


@router.get("/analytics")
async def analytics(
    request: Request,
    db: AsyncSession = Depends(get_db),
    sid: str = Depends(session_id),
    scope: str = Query("session", pattern="^(session|all)$"),
    days: int = Query(30, ge=1, le=365),
    interval: str = Query("day", pattern="^(day|week|month)$"),
) -> dict:
    if scope == "all" and not is_admin(request):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "admin_passcode_required",
                "message": "Enter the demo admin passcode to analyse every session.",
            },
        )

    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)

    rows = list(await db.scalars(select(Conversation)))
    if scope == "session":
        rows = [row for row in rows if row.session_id == sid]
    # A conversation is dated by when it started, so chatting further never moves
    # it into a different day.
    in_range = [row for row in rows if start <= _as_utc(row.created_at).date() <= today]

    intents = Counter(row.intent or "unknown" for row in in_range)
    dispositions = Counter(row.disposition or "other" for row in in_range)
    outcomes = Counter(row.last_outcome or "none" for row in in_range)

    intent_order = INTENT_ORDER + (("unknown",) if intents.get("unknown") else ())
    per_bucket: dict[str, Counter] = defaultdict(Counter)
    for row in in_range:
        bucket = _bucket_key(_as_utc(row.created_at), interval)
        per_bucket[bucket][row.intent or "unknown"] += 1

    total = len(in_range)
    return {
        "scope": scope,
        "range": {
            "from": start.isoformat(),
            "to": today.isoformat(),
            "days": days,
            "interval": interval,
        },
        "totals": {"conversations": total},
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
        "series": [
            {
                "bucket": bucket,
                "label": _bucket_label(bucket, interval),
                "total": sum(per_bucket[bucket].values()),
                "counts": {
                    intent: per_bucket[bucket].get(intent, 0) for intent in intent_order
                },
            }
            for bucket in _bucket_starts(start, today, interval)
        ],
        "intent_labels": INTENT_LABELS,
    }
