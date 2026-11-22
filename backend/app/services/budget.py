"""Spend and message guards.

A public demo link with a server-side key is an open wallet, so the host key is
metered three ways: per session, per day, and per IP. Visitors who supply their
own key (BYOK) bypass the cost caps -- it is their wallet.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import settings
from ..models import Event, Session


class LimitExceeded(Exception):
    """Raised when a demo guard trips. Carries a visitor-friendly message."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


async def spend_today(db: AsyncSession) -> float:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    total = await db.scalar(
        select(func.coalesce(func.sum(Event.cost_usd), 0.0)).where(Event.created_at >= start)
    )
    return round(float(total or 0.0), 6)


def message_cap(byok: bool) -> int:
    return settings.BYOK_SESSION_MESSAGE_CAP if byok else settings.SESSION_MESSAGE_CAP


async def check_limits(db: AsyncSession, session: Session, *, byok: bool) -> dict:
    """Raise ``LimitExceeded`` if the visitor has used up their allowance."""
    cap = message_cap(byok)
    if session.message_count >= cap:
        raise LimitExceeded(
            "session_message_cap",
            (
                f"This demo session has reached its {cap}-message limit. "
                "Reset the conversation to start fresh, or paste your own OpenRouter key "
                "to keep going."
            ),
        )

    daily = await spend_today(db)
    if not byok and daily >= settings.MAX_DAILY_SPEND_USD:
        raise LimitExceeded(
            "daily_budget",
            (
                f"The demo has reached its daily model budget (${settings.MAX_DAILY_SPEND_USD:.2f}). "
                "Paste your own OpenRouter key to keep exploring."
            ),
        )

    return {
        "key_mode": "byok" if byok else "host",
        "messages_used": session.message_count,
        "message_cap": cap,
        "session_cost_usd": round(session.total_cost_usd, 4),
        "daily_cost_usd": daily,
        "daily_budget_usd": settings.MAX_DAILY_SPEND_USD,
    }


# --- per-IP burst limiting -------------------------------------------------
_hits: dict[str, deque[float]] = defaultdict(deque)


def check_rate_limit(ip: str) -> None:
    limit = settings.IP_RATE_LIMIT_PER_MINUTE
    if limit <= 0:
        return
    now = time.time()
    bucket = _hits[ip]
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= limit:
        raise LimitExceeded(
            "ip_rate_limit",
            "Whoa -- that's a lot of requests. Give it a few seconds and try again.",
        )
    bucket.append(now)
