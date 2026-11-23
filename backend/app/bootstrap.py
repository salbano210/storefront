"""First-boot seeding.

Both the brand configuration and the creator profiles live in the database and
are seeded from the repo fixtures when the tables are empty. Anything that needs
a usable database -- the app lifespan, the test harness, the demo scripts --
calls this instead of seeding the two tables separately.

Optionally it also generates sample conversation history, so the logs and the
dashboard are not empty on a fresh deployment. See ``SEED_DEMO_HISTORY``.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import settings
from .models import Conversation
from .services import config_manager, data_store

log = logging.getLogger("storefront.bootstrap")


async def bootstrap(db: AsyncSession) -> int:
    """Seed config + creator profiles if needed. Returns the live config version."""
    version, _ = await config_manager.ensure_seeded(db)
    await data_store.seed(db)
    await maybe_seed_sample_history(db)
    return version


async def maybe_seed_sample_history(db: AsyncSession) -> None:
    """Generate sample history at boot, if asked and if there is not enough.

    Skipped once the database already holds as much as ``SEED_DEMO_COUNT`` asks for, so
    a redeploy on a persistent database does not pile more on top -- but raising the
    count does take effect, instead of being ignored because *some* history exists. It
    only ever replaces sample rows. A failure here is logged and swallowed: a demo with
    an empty dashboard still works, an app that will not boot does not.
    """
    if not settings.SEED_DEMO_HISTORY:
        return

    from .services import seeder

    try:
        existing = await db.scalar(
            select(func.count(Conversation.id)).where(
                Conversation.source == seeder.SAMPLE_SOURCE
            )
        )
        if existing >= settings.SEED_DEMO_COUNT:
            return
        summary = await seeder.seed_history(
            db, count=settings.SEED_DEMO_COUNT, days=settings.SEED_DEMO_DAYS
        )
        log.info(
            "seeded %s sample conversations over %s days",
            summary["conversations"],
            settings.SEED_DEMO_DAYS,
        )
    except Exception:  # noqa: BLE001 - a populated demo is not worth a failed boot
        log.exception("sample history generation failed; continuing without it")
