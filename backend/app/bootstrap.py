"""First-boot seeding.

Both the brand configuration and the creator profiles live in the database and
are seeded from the repo fixtures when the tables are empty. Anything that needs
a usable database -- the app lifespan, the test harness, the demo scripts --
calls this instead of seeding the two tables separately.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from .services import config_manager, data_store


async def bootstrap(db: AsyncSession) -> int:
    """Seed config + creator profiles if needed. Returns the live config version."""
    version, _ = await config_manager.ensure_seeded(db)
    await data_store.seed(db)
    return version
