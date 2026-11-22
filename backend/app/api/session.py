"""Session + budget endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from .. import settings
from ..db import get_db
from ..services import budget, conversation_store as store
from .deps import require_admin, session_id

router = APIRouter(prefix="/api", tags=["session"])


@router.get("/session")
async def get_session(
    db: AsyncSession = Depends(get_db),
    sid: str = Depends(session_id),
) -> dict:
    row = await store.get_or_create_session(db, sid)
    return {
        "session_id": row.id,
        "host_key_available": settings.HOST_KEY_AVAILABLE,
        "admin_required": bool(settings.ADMIN_PASSCODE),
        "message_count": row.message_count,
        "message_cap": settings.SESSION_MESSAGE_CAP,
        "session_cost_usd": round(row.total_cost_usd, 4),
        "daily_cost_usd": await budget.spend_today(db),
        "daily_budget_usd": settings.MAX_DAILY_SPEND_USD,
        "tokens_in": row.tokens_in,
        "tokens_out": row.tokens_out,
    }


@router.post("/session/reset")
async def reset_session(
    db: AsyncSession = Depends(get_db),
    sid: str = Depends(session_id),
) -> dict:
    row = await store.get_or_create_session(db, sid)
    row.message_count = 0
    await db.commit()
    return {"ok": True, "session_id": row.id, "message_count": row.message_count}


@router.get("/admin/check")
async def admin_check(_: str = Depends(require_admin)) -> dict:
    return {"ok": True, "admin": True}
