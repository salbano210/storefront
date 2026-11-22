"""Ad-hoc debugging harness (not part of the test suite).

Usage: .venv/bin/python scripts/debug_turn.py "<message>" [creator_id]
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

os.environ.setdefault("OPENROUTER_API_KEY", "sk-or-v1-testkey")
os.environ.setdefault("ADMIN_PASSCODE", "test-passcode")
os.environ.setdefault("STATIC_DIR", "")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="myshop-debug-"))

from httpx import ASGITransport, AsyncClient  # noqa: E402

sys.path.insert(0, str(BACKEND / "tests"))
from fake_llm import FakeLLM  # noqa: E402

from app.db import AsyncSessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import config_manager, llm_client  # noqa: E402


async def main() -> None:
    message = sys.argv[1] if len(sys.argv) > 1 else "I'd love to partner with Demo Brand."
    creator_id = sys.argv[2] if len(sys.argv) > 2 else "creator_005"

    await init_db()
    async with AsyncSessionLocal() as db:
        await config_manager.ensure_seeded(db)

    fake = None
    if os.environ.get("REAL_LLM") != "1":
        fake = FakeLLM(force_wrong_tag=True)
        llm_client.build_llm = lambda *args, **kwargs: fake

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://debug") as client:
        response = await client.post(
            "/api/chat", json={"creator_id": creator_id, "message": message}
        )
        print("STATUS", response.status_code)
        payload = response.json()
        print("OUTCOME", json.dumps(payload.get("outcome"), indent=2))
        print("REPLY", payload.get("reply"))
        logs = (
            await client.get(
                "/api/logs",
                params={"conversation_id": payload["conversation_id"], "limit": 500},
            )
        ).json()
        for event in logs["events"]:
            print(f"  [{event['id']:>3}] {event['type']:<22} {event['status']:<8} {event['summary']}")
            if event["type"] == "llm_response" and event.get("payload"):
                payload = event["payload"]
                print(
                    f"        model={payload.get('model')} tools_available={payload.get('tools_available')} "
                    f"tool_calls={payload.get('tool_calls')}"
                )
                if payload.get("text"):
                    print(f"        text={payload['text'][:200]!r}")
        if fake is not None:
            print("LLM CALLS", json.dumps(fake.calls, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
