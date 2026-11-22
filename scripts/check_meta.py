"""Live check of the meta endpoints (/api/models, /api/key/test)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

os.environ.setdefault("STATIC_DIR", "")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="storefront-live-"))

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.main import app  # noqa: E402

ADMIN = {"X-Admin-Passcode": os.environ.get("ADMIN_PASSCODE", "smoke-passcode")}


async def main() -> None:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://live") as client:
        response = await client.get("/api/models")
        payload = response.json()
        print(
            f"GET /api/models -> {response.status_code}, {len(payload['models'])} tool-capable models, "
            f"recommended={payload['recommended'][:3]}"
        )
        cheapest = sorted(
            payload["models"], key=lambda m: (m["prompt_price_per_m"] or 99)
        )[:3]
        for model in cheapest:
            print(
                f"   cheapest: {model['id']} in ${model['prompt_price_per_m']}/M "
                f"out ${model['completion_price_per_m']}/M"
            )

        if key:
            response = await client.post("/api/key/test", headers=ADMIN)
            print(f"POST /api/key/test (admin) -> {response.status_code} {json.dumps(response.json())[:300]}")
            unlocked = await client.post("/api/key/test")
            print(f"POST /api/key/test (no passcode) -> {unlocked.status_code}")

        response = await client.post("/api/key/test")
        print(
            f"POST /api/key/test (no passcode) -> {response.status_code} "
            f"code={response.json().get('detail', {}).get('code')}"
        )


if __name__ == "__main__":
    asyncio.run(main())
