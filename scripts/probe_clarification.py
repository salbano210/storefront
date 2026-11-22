"""Check that an unnamed product request produces a question, not a surprise gift."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

os.environ.setdefault("STATIC_DIR", "")
os.environ.setdefault("ADMIN_PASSCODE", "probe")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="storefront-clarify-"))
os.environ.setdefault("SESSION_MESSAGE_CAP", "100")
os.environ.setdefault("MAX_DAILY_SPEND_USD", "50")
os.environ.setdefault("IP_RATE_LIMIT_PER_HOUR", "0")
os.environ.setdefault("IP_RATE_LIMIT_PER_MINUTE", "0")

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.bootstrap import bootstrap  # noqa: E402
from app.db import AsyncSessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402

ASK = "Hey, can I get a free gift?"
ATTEMPTS = 3
CREATORS = ("creator_002", "creator_001", "creator_005")


async def main() -> int:
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY not set")
        return 2

    await init_db()
    async with AsyncSessionLocal() as db:
        await bootstrap(db)

    failures = 0
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://clarify", timeout=120.0) as client:
        for index in range(ATTEMPTS):
            creator_id = CREATORS[index % len(CREATORS)]
            payload = (
                await client.post(
                    "/api/chat", json={"creator_id": creator_id, "message": ASK}
                )
            ).json()
            asked = "?" in payload["reply"]
            outcome = payload["outcome"]["type"]
            print(f"\n{index + 1}. {creator_id} -> outcome={outcome} asked={asked}")
            print(f"   {payload['reply'][:240]}")
            if outcome == "gift_issued":
                print("   FAIL: issued a gift without being told which product")
                failures += 1
            elif not asked:
                print("   FAIL: neither asked nor issued")
                failures += 1

    print(f"\n{ATTEMPTS - failures}/{ATTEMPTS} asked which product instead of guessing")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
