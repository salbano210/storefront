"""Generate (or clear) sample conversation history against a local database.

    cd backend
    .venv/bin/python ../scripts/seed_history.py --days 30 --count 60
    .venv/bin/python ../scripts/seed_history.py --clear

It drives the real orchestrator with the deterministic model, so it needs no API
key and costs nothing. The conversations it writes are marked ``source="seeded"``,
and are labelled as sample data in the UI.

On the deployed demo the same thing is available as
``POST /api/admin/sample-history`` (admin passcode), which seeds into the calling
browser's own session.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from app.bootstrap import bootstrap  # noqa: E402
from app.db import AsyncSessionLocal, init_db  # noqa: E402
from app.services import seeder  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30, help="how far back to spread it")
    parser.add_argument("--count", type=int, default=60, help="how many conversations")
    parser.add_argument("--seed", type=int, default=7, help="deterministic RNG seed")
    parser.add_argument("--clear", action="store_true", help="remove sample history and stop")
    args = parser.parse_args()

    await init_db()
    async with AsyncSessionLocal() as db:
        await bootstrap(db)
        if args.clear:
            print(json.dumps({"removed": await seeder.clear_sample_history(db)}, indent=2))
            return
        summary = await seeder.seed_history(
            db, count=args.count, days=args.days, seed=args.seed
        )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
