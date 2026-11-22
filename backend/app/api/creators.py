"""Creator endpoints.

Card data is public. The verified profile (revenue, sales, shipping address) is
admin-only -- and the agents always read it from the backend, never from input.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..schemas import CreatorCard
from ..services import data_store
from .deps import require_admin

router = APIRouter(prefix="/api/creators", tags=["creators"])

CARD_FIELDS = set(CreatorCard.model_fields.keys())


@router.get("")
async def list_creators() -> dict:
    return {"creators": [card.model_dump() for card in data_store.list_creator_cards()]}


@router.get("/{creator_id}")
async def get_creator(creator_id: str) -> dict:
    profile = data_store.get_creator(creator_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"unknown creator '{creator_id}'")
    return {"creator": profile.model_dump(include=CARD_FIELDS)}


@router.get("/{creator_id}/verified")
async def get_verified_profile(creator_id: str, _: str = Depends(require_admin)) -> dict:
    profile = data_store.get_creator(creator_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"unknown creator '{creator_id}'")
    return {"creator": profile.model_dump()}
