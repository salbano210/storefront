"""Creator endpoints.

Card data is public. The verified profile (revenue, sales, shipping address) and
every edit are admin-only -- the agents always read profiles from the backend,
never from anything the creator or the browser supplies.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..schemas import CreatorCard, CreatorProfile
from ..services import data_store
from ..services import event_logger as events
from ..services.event_logger import EventLogger
from .deps import is_admin, require_admin, session_id

router = APIRouter(prefix="/api/creators", tags=["creators"])

CARD_FIELDS = set(CreatorCard.model_fields.keys())


def _forbidden() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={
            "code": "admin_passcode_required",
            "message": "Enter the demo admin passcode to edit or view verified profiles.",
        },
    )


@router.get("")
async def list_creators(
    request: Request,
    full: bool = Query(False, description="Return full verified profiles (admin only)"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if full:
        if not is_admin(request):
            raise _forbidden()
        profiles = await data_store.list_profiles(db)
        return {"creators": [profile.model_dump() for profile in profiles], "full": True}
    cards = await data_store.list_cards(db)
    return {"creators": [card.model_dump() for card in cards], "full": False}


@router.post("/reset")
async def reset_creators(
    db: AsyncSession = Depends(get_db),
    sid: str = Depends(session_id),
    _: str = Depends(require_admin),
) -> dict:
    """Restore every profile from the repo fixtures."""
    profiles = await data_store.reset(db)
    await EventLogger(db, session_id=sid).log(
        events.CREATOR_RESET,
        agent="admin",
        summary=f"Creator profiles restored to repo defaults ({len(profiles)} profiles)",
        payload={"creators": [{"id": profile.id, "name": profile.name} for profile in profiles]},
    )
    return {"creators": [profile.model_dump() for profile in profiles]}


@router.put("/{creator_id}")
async def update_creator(
    creator_id: str,
    payload: CreatorProfile,
    db: AsyncSession = Depends(get_db),
    sid: str = Depends(session_id),
    _: str = Depends(require_admin),
) -> dict:
    """Edit a creator's verified profile. These are the numbers the agents use."""
    if payload.id != creator_id:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "creator_id_mismatch",
                "message": f"Body id '{payload.id}' does not match the path '{creator_id}'.",
            },
        )

    before = await data_store.get_creator(db, creator_id)
    if before is None:
        raise HTTPException(status_code=404, detail=f"unknown creator '{creator_id}'")

    saved = await data_store.save_profile(db, payload)

    previous = before.model_dump()
    changes = {
        key: {"from": previous.get(key), "to": value}
        for key, value in saved.model_dump().items()
        if previous.get(key) != value
    }

    await EventLogger(db, session_id=sid, creator_id=creator_id).log(
        events.CREATOR_UPDATED,
        agent="admin",
        summary=(
            f"Verified profile updated for {saved.name}: "
            + (", ".join(changes) if changes else "no field changes")
        ),
        payload={
            "creator_id": creator_id,
            "changed_fields": changes,
            "note": "These values are what the agents decide on.",
        },
    )
    return {"creator": saved.model_dump(), "changed_fields": sorted(changes)}


@router.get("/{creator_id}")
async def get_creator(creator_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    profile = await data_store.get_creator(db, creator_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"unknown creator '{creator_id}'")
    return {"creator": profile.model_dump(include=CARD_FIELDS)}


@router.get("/{creator_id}/verified")
async def get_verified_profile(
    creator_id: str, db: AsyncSession = Depends(get_db), _: str = Depends(require_admin)
) -> dict:
    profile = await data_store.get_creator(db, creator_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"unknown creator '{creator_id}'")
    return {"creator": profile.model_dump()}
