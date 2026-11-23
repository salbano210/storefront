"""Sample-history endpoints: fill the logs and the dashboard with real execution.

Generating history writes rows, so it is admin-gated. The conversations it makes are
marked ``source="seeded"`` and labelled in the UI.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..services import event_logger as events
from ..services import seeder
from ..services.event_logger import EventLogger
from .deps import require_admin, session_id

router = APIRouter(prefix="/api/admin", tags=["admin"])


class SeedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: int = Field(30, ge=1, le=365)
    count: int = Field(60, ge=1, le=400)
    seed: int = Field(7, ge=0, le=1_000_000)


@router.post("/sample-history")
async def create_sample_history(
    payload: SeedRequest,
    db: AsyncSession = Depends(get_db),
    sid: str = Depends(session_id),
    _: str = Depends(require_admin),
) -> dict:
    """Generate sample conversations, replacing any sample history already present.

    Each conversation gets its own session, so the per-session duplicate guard
    behaves as it does in real life. Both the logs and the dashboard show sample
    conversations in their default scope, labelled.
    """
    summary = await seeder.seed_history(
        db, count=payload.count, days=payload.days, seed=payload.seed
    )
    await EventLogger(db, session_id=sid).log(
        events.SAMPLE_HISTORY_GENERATED,
        agent="admin",
        summary=(
            f"Generated {summary['conversations']} sample conversations over "
            f"{payload.days} days"
        ),
        payload={"kind": "sample_history", **summary},
    )
    return summary


@router.delete("/sample-history")
async def delete_sample_history(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_admin),
) -> dict:
    """Remove every sample conversation and everything it produced."""
    return {"removed": await seeder.clear_sample_history(db)}
