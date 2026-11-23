"""Editing verified creator metrics from the Creators page."""

from __future__ import annotations

import pytest
from conftest import ADMIN_HEADERS

from app.db import AsyncSessionLocal
from app.services import data_store

pytestmark = pytest.mark.asyncio

SERUM_REQUEST = {"message": "Can I get the Barrier Repair Serum?"}


async def _profile(client, creator_id: str) -> dict:
    response = await client.get("/api/creators", params={"full": True}, headers=ADMIN_HEADERS)
    assert response.status_code == 200, response.text
    return next(item for item in response.json()["creators"] if item["id"] == creator_id)


async def test_profiles_are_seeded_into_the_database():
    """At runtime the database, not the JSON fixture, is the source of truth."""
    async with AsyncSessionLocal() as db:
        profile = await data_store.get_creator(db, "creator_001")
        assert profile is not None
        assert profile.name == "Kathryn Bigelow"
        assert profile.followers == 128400


async def test_cards_stay_public_and_hide_private_fields(client):
    payload = (await client.get("/api/creators")).json()
    assert len(payload["creators"]) == 6
    card = payload["creators"][0]
    assert "followers" in card
    assert "shipping_address" not in card
    assert "brand_attributed_revenue" not in card


async def test_full_profiles_require_the_passcode(client):
    locked = await client.get("/api/creators", params={"full": True})
    assert locked.status_code == 401
    assert locked.json()["detail"]["code"] == "admin_passcode_required"

    unlocked = await client.get("/api/creators", params={"full": True}, headers=ADMIN_HEADERS)
    assert unlocked.status_code == 200
    assert unlocked.json()["full"] is True


async def test_editing_metrics_requires_the_passcode(client):
    profile = await _profile(client, "creator_001")
    assert (await client.put("/api/creators/creator_001", json=profile)).status_code == 401


async def test_editing_metrics_changes_an_agent_decision(client, use_fake_llm):
    """The whole point of the editor: the agents decide on the edited numbers."""
    use_fake_llm()

    before = await client.post("/api/chat", json={"creator_id": "creator_003", **SERUM_REQUEST})
    assert before.json()["outcome"]["type"] == "gift_declined"

    profile = await _profile(client, "creator_003")
    profile["followers"] = 60000
    profile["brand_attributed_revenue"] = 5000
    updated = await client.put(
        "/api/creators/creator_003", json=profile, headers=ADMIN_HEADERS
    )
    assert updated.status_code == 200, updated.text
    assert "followers" in updated.json()["changed_fields"]
    assert "brand_attributed_revenue" in updated.json()["changed_fields"]

    opening = await client.post("/api/chat", json={"creator_id": "creator_003", **SERUM_REQUEST})
    opening_payload = opening.json()
    # Eligible now -- the address still has to be settled before the order.
    assert opening_payload["outcome"]["type"] == "details_required", opening.text

    after = await client.post(
        "/api/chat",
        json={
            "creator_id": "creator_003",
            "message": "Yes, that address is right.",
            "conversation_id": opening_payload["conversation_id"],
        },
    )
    assert after.json()["outcome"]["type"] == "gift_issued", after.text

    events = (await client.get("/api/logs", params={"event_type": "creator_updated"})).json()
    assert events["count"] == 1
    changed = events["events"][0]["payload"]["changed_fields"]
    assert changed["followers"] == {"from": 7840, "to": 60000}

    assert (await client.post("/api/creators/reset", headers=ADMIN_HEADERS)).status_code == 200


async def test_invalid_metrics_are_rejected(client):
    profile = await _profile(client, "creator_001")

    profile["engagement_rate"] = 4.5  # 450% engagement is not a thing
    assert (
        await client.put("/api/creators/creator_001", json=profile, headers=ADMIN_HEADERS)
    ).status_code == 422

    profile["engagement_rate"] = 0.05
    profile["followers"] = -1
    assert (
        await client.put("/api/creators/creator_001", json=profile, headers=ADMIN_HEADERS)
    ).status_code == 422


async def test_id_mismatch_is_rejected(client):
    profile = await _profile(client, "creator_001")
    profile["id"] = "creator_002"
    response = await client.put("/api/creators/creator_001", json=profile, headers=ADMIN_HEADERS)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "creator_id_mismatch"


async def test_reset_restores_the_fixture_metrics(client):
    profile = await _profile(client, "creator_005")
    profile["followers"] = 1
    assert (
        await client.put("/api/creators/creator_005", json=profile, headers=ADMIN_HEADERS)
    ).status_code == 200
    assert (await _profile(client, "creator_005"))["followers"] == 1

    assert (await client.post("/api/creators/reset", headers=ADMIN_HEADERS)).status_code == 200
    assert (await _profile(client, "creator_005"))["followers"] == 214000


async def test_audience_demographics_survive_an_edit(client):
    profile = await _profile(client, "creator_001")
    profile["primary_age_range"] = "30-40"
    await client.put("/api/creators/creator_001", json=profile, headers=ADMIN_HEADERS)
    after = await _profile(client, "creator_001")
    assert after["primary_age_range"] == "30-40"
    assert after["audience"]["top_gender"] == "female"  # untouched nested data preserved
    await client.post("/api/creators/reset", headers=ADMIN_HEADERS)
