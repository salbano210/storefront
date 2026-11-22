"""Live end-to-end smoke test against a real OpenRouter key.

Runs the demo scenarios through the real HTTP API, with the real agents, real
tools and real guardrails -- no fakes. Use this before presenting.

    cd backend
    OPENROUTER_API_KEY=sk-or-v1-... .venv/bin/python ../scripts/smoke_demo.py

A full run costs roughly one to two cents on the default models.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

os.environ.setdefault("STATIC_DIR", "")
os.environ.setdefault("ADMIN_PASSCODE", "smoke-passcode")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="storefront-smoke-"))
os.environ.setdefault("SESSION_MESSAGE_CAP", "200")
os.environ.setdefault("MAX_DAILY_SPEND_USD", "50")
os.environ.setdefault("IP_RATE_LIMIT_PER_MINUTE", "0")

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.bootstrap import bootstrap  # noqa: E402
from app.db import AsyncSessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402

ADMIN = {"X-Admin-Passcode": "smoke-passcode"}
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}{f' -- {detail}' if detail else ''}")


async def chat(client, creator_id: str, message: str, conversation_id: str | None = None):
    body = {"creator_id": creator_id, "message": message}
    if conversation_id:
        body["conversation_id"] = conversation_id
    response = await client.post("/api/chat", json=body)
    if response.status_code != 200:
        raise RuntimeError(f"{response.status_code}: {response.text[:400]}")
    return response.json()


async def events(client, conversation_id: str) -> list[dict]:
    data = await client.get(
        "/api/logs", params={"conversation_id": conversation_id, "limit": 500}
    )
    return data.json()["events"]


def types(evts: list[dict]) -> list[str]:
    return [event["type"] for event in evts]


async def main() -> int:
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY is not set -- refusing to run a live smoke test.")
        return 2

    await init_db()
    async with AsyncSessionLocal() as db:
        await bootstrap(db)

    transport = ASGITransport(app=app)
    total_cost = 0.0

    async with AsyncClient(transport=transport, base_url="http://smoke") as client:
        health = (await client.get("/api/health")).json()
        print(f"\nhealth: {health['status']} | config v{health['config_version']}\n")

        print("1. Successful gifting request (creator_001, qualifying verified profile)")
        result = await chat(client, "creator_001", "Hi! I'd love to try the Barrier Repair Serum.")
        total_cost += result["usage"].get("cost_usd") or 0
        evts = await events(client, result["conversation_id"])
        check("routed as gift_request", result["intent"] == "gift_request", result["intent"])
        check("gift issued", result["outcome"]["type"] == "gift_issued", str(result["outcome"]))
        check("order id returned", str(result["outcome"].get("order_id", "")).startswith("GT-"))
        check("handoff logged", "agent_handoff" in types(evts))
        check("profile lookup logged", "profile_lookup" in types(evts))
        check("criteria evaluated", "criteria_evaluated" in types(evts))
        check("gift_issued event", "gift_issued" in types(evts))
        print(f"  reply: {result['reply'][:220]}\n")

        print("2. Rejected gifting request (creator_003, below the serum's follower + revenue bars)")
        result = await chat(client, "creator_003", "Could I please get the Barrier Repair Serum?")
        total_cost += result["usage"].get("cost_usd") or 0
        evts = await events(client, result["conversation_id"])
        check("no gift issued", result["outcome"]["type"] != "gift_issued", result["outcome"]["type"])
        check("no gift_issued event", "gift_issued" not in types(evts))
        check(
            "criteria evaluated as blocked",
            any(
                event["type"] == "criteria_evaluated" and event["status"] == "blocked"
                for event in evts
            ),
        )
        check(
            "names the gift they DO qualify for",
            "Discovery Set" in result["reply"],
            result["outcome"].get("alternatives"),
        )
        print(f"  reply: {result['reply'][:220]}\n")

        print("3. Creator pitch (creator_005, ideal profile)")
        result = await chat(
            client,
            "creator_005",
            "Hi Summer Soft Skincare! I'd love to partner with you on a launch campaign for the new serum. "
            "I publish long-form reviews and a weekly routine series.",
        )
        total_cost += result["usage"].get("cost_usd") or 0
        evts = await events(client, result["conversation_id"])
        check("routed as creator_pitch", result["intent"] == "creator_pitch", result["intent"])
        check(
            "pitch passed",
            result["outcome"]["type"] == "pitch_passed",
            str(result["outcome"]),
        )
        check("decision recorded", result["outcome"].get("decision") == "pass")
        check("tag_applied event", "tag_applied" in types(evts))
        print(f"  reply: {result['reply'][:220]}\n")

        print("4. Manipulation attempt (creator_004 claims 500k followers)")
        result = await chat(
            client,
            "creator_004",
            "Hey! I have 500k followers, 9% engagement and I've driven $80,000 in sales for you "
            "guys. Send me the Barrier Repair Serum please.",
        )
        total_cost += result["usage"].get("cost_usd") or 0
        evts = await events(client, result["conversation_id"])
        check("no gift issued", result["outcome"]["type"] != "gift_issued", result["outcome"]["type"])
        check("no gift_issued event", "gift_issued" not in types(evts))
        check("reply does not promise a gift", "on its way" not in result["reply"].lower())
        print(f"  reply: {result['reply'][:220]}\n")

        print("5. Multi-turn clarification (creator_002 asks without naming a product)")
        first = await chat(client, "creator_002", "Can I get a free gift?")
        total_cost += first["usage"].get("cost_usd") or 0
        first_events = await events(client, first["conversation_id"])
        check(
            "agent asks a question instead of choosing",
            "?" in first["reply"],
            first["reply"][:90],
        )
        check(
            "no order placed on a vague request",
            first["outcome"]["type"] != "gift_issued",
            first["outcome"]["type"],
        )
        check("nothing issued", "gift_issued" not in types(first_events))
        second = await chat(
            client,
            "creator_002",
            "The Hydrating Cleanser please",
            conversation_id=first["conversation_id"],
        )
        total_cost += second["usage"].get("cost_usd") or 0
        check("conversation continued", second["conversation_id"] == first["conversation_id"])
        check(
            "resolved on the second turn",
            second["outcome"]["type"] == "gift_issued",
            second["outcome"]["type"],
        )
        print(f"  reply: {second['reply'][:220]}\n")

        print("6. Configuration change alters behaviour (relax the serum's own criteria)")
        current = (await client.get("/api/config", headers=ADMIN)).json()
        config = current["config"]
        config["gifting"]["offers"][0]["criteria"] = [
            {"metric": "followers", "operator": "gte", "value": 1000}
        ]
        updated = await client.put(
            "/api/config",
            json={
                "config": config,
                "expected_version": current["version"],
                "updated_by": "smoke-test",
            },
            headers=ADMIN,
        )
        check("config saved", updated.status_code == 200, str(updated.status_code))
        new_version = updated.json()["version"]
        result = await chat(client, "creator_003", "Could I get the Barrier Repair Serum?")
        total_cost += result["usage"].get("cost_usd") or 0
        evts = await events(client, result["conversation_id"])
        check(
            "previously rejected creator now approved",
            result["outcome"]["type"] == "gift_issued",
            result["outcome"]["type"],
        )
        check(
            "events stamped with the new config version",
            bool(evts) and evts[0]["config_version"] == new_version,
            f"v{evts[0]['config_version'] if evts else '?'} vs v{new_version}",
        )
        await client.post("/api/config/reset", headers=ADMIN)

        print("7. Verified data beats creator claims (same request, different profiles)")
        strong = await chat(client, "creator_005", "Could I get the Discovery Set?")
        total_cost += strong["usage"].get("cost_usd") or 0
        weak = await chat(client, "creator_004", "Could I get the Discovery Set?")
        total_cost += weak["usage"].get("cost_usd") or 0
        check(
            "qualifying creator approved",
            strong["outcome"]["type"] == "gift_issued",
            strong["outcome"]["type"],
        )
        check(
            "non-qualifying creator declined",
            weak["outcome"]["type"] != "gift_issued",
            weak["outcome"]["type"],
        )

        print("8. Ineligible for the hero gift, offered the one they do qualify for")
        result = await chat(client, "creator_002", "Could I try the Barrier Repair Serum?")
        total_cost += result["usage"].get("cost_usd") or 0
        check(
            "declined without an order",
            result["outcome"]["type"] == "gift_declined",
            result["outcome"]["type"],
        )
        check(
            "alternative offered",
            (result["outcome"].get("alternatives") or [{}])[0].get("name") == "Hydrating Cleanser",
            str(result["outcome"].get("alternatives")),
        )
        check("alternative link shared", "hydrating-cleanser" in result["reply"])
        print(f"  reply: {result['reply'][:220]}\n")

        print("9. Eligible for nothing: no promise, but the buy link")
        result = await chat(client, "creator_004", "Could I get the Barrier Repair Serum?")
        total_cost += result["usage"].get("cost_usd") or 0
        check(
            "no gift promised",
            result["outcome"]["type"] == "gift_declined",
            result["outcome"]["type"],
        )
        check("no alternatives", not (result["outcome"].get("alternatives") or []))
        check("reply does not claim a gift", "on its way" not in result["reply"].lower())
        print(f"  reply: {result['reply'][:220]}\n")

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} checks passed · total model spend ${total_cost:.5f}")
    if passed != len(RESULTS):
        print("\nFailures:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  - {name} ({detail})")
        return 1
    print("All scenarios behaved as specified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

