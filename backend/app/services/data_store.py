"""Creator data: profiles live in the database, seeded from JSON fixtures.

The fixtures in ``app/data`` are the *seed*. Once seeded, the database is the
source of truth, which is what lets a brand admin edit the verified metrics from
the Creators page -- and what makes "the agents decide on backend data" literally
true rather than aspirational.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CreatorProfileRow
from ..schemas import CreatorCard, CreatorProfile

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CREATORS_PATH = DATA_DIR / "creators.json"


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def seed_profiles() -> list[CreatorProfile]:
    """The repo fixtures as validated profiles."""
    return [CreatorProfile(**item) for item in _read_json(CREATORS_PATH).get("creators", [])]


async def seed(db: AsyncSession) -> int:
    """Populate the table from the fixtures if it is empty. Returns rows added."""
    existing = await db.scalar(select(CreatorProfileRow.creator_id).limit(1))
    if existing:
        return 0
    profiles = seed_profiles()
    for sort_order, profile in enumerate(profiles):
        db.add(
            CreatorProfileRow(
                creator_id=profile.id,
                sort_order=sort_order,
                profile_json=profile.model_dump_json(),
            )
        )
    await db.commit()
    return len(profiles)


async def list_profiles(db: AsyncSession) -> list[CreatorProfile]:
    rows = await db.scalars(select(CreatorProfileRow).order_by(CreatorProfileRow.sort_order))
    return [CreatorProfile(**json.loads(row.profile_json)) for row in rows]


async def list_cards(db: AsyncSession) -> list[CreatorCard]:
    return [CreatorCard(**profile.model_dump()) for profile in await list_profiles(db)]


async def get_creator(db: AsyncSession, creator_id: str) -> CreatorProfile | None:
    row = await db.get(CreatorProfileRow, creator_id)
    return CreatorProfile(**json.loads(row.profile_json)) if row else None


async def save_profile(db: AsyncSession, profile: CreatorProfile) -> CreatorProfile:
    row = await db.get(CreatorProfileRow, profile.id)
    if row is None:
        next_order = int(await db.scalar(select(func.count()).select_from(CreatorProfileRow)) or 0)
        db.add(
            CreatorProfileRow(
                creator_id=profile.id,
                sort_order=next_order,
                profile_json=profile.model_dump_json(),
            )
        )
    else:
        row.profile_json = profile.model_dump_json()
    await db.commit()
    return profile


async def reset(db: AsyncSession) -> list[CreatorProfile]:
    """Restore every profile from the repo fixtures."""
    await db.execute(delete(CreatorProfileRow))
    await db.commit()
    await seed(db)
    return await list_profiles(db)
