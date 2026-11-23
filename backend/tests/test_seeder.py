"""Sample history: real execution underneath synthetic wording and timestamps.

The point of these tests is that the sample data is not hand-written rows. Every
event in it was emitted by the same orchestrator, tools and guardrails that serve a
real turn, so the logs and the dashboard are exercised end to end.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest
from conftest import ADMIN_HEADERS
from sqlalchemy import func, select

from app.db import AsyncSessionLocal
from app.models import Conversation
from app.services import seeder

# pytest runs in asyncio auto mode, so only the tests that need it are async.

# Derived from the recipes, so adding one cannot silently make these too small:
# `count` is clamped to at least the number of recipes, and every recipe is
# planned exactly once when the count equals the total weight.
FULL = sum(recipe.weight for recipe in seeder.RECIPES)
LIGHT = len(seeder.RECIPES)


async def seed(client, **body) -> dict:
    payload = {"count": FULL, "days": 30, "seed": 7, **body}
    response = await client.post("/api/admin/sample-history", json=payload, headers=ADMIN_HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


async def analytics(client, **params) -> dict:
    response = await client.get("/api/analytics", params={"days": 90, **params})
    assert response.status_code == 200, response.text
    return response.json()


async def chat(client, creator_id: str, message: str) -> dict:
    response = await client.post("/api/chat", json={"creator_id": creator_id, "message": message})
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Generating
# ---------------------------------------------------------------------------
async def test_generating_history_needs_the_admin_passcode(client):
    response = await client.post("/api/admin/sample-history", json={"count": 20})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "admin_passcode_required"


async def test_it_fills_the_dashboard_with_a_realistic_mix(client, use_fake_llm):
    use_fake_llm()
    summary = await seed(client)

    assert summary["errors"] == []
    assert summary["conversations"] == FULL
    # Every outcome the recipes describe, which between them cover all three
    # dispositions and both intents.
    assert set(summary["outcomes"]) == {
        "gift_issued",
        "gift_duplicate",
        "gift_declined",
        "details_required",
        "inquiry",
        "escalated",
    }
    # Spread across creators, so the logs' creator filter has something in it.
    assert len(summary["by_creator"]) == 5

    data = await analytics(client)
    dispositions = {row["disposition"]: row["count"] for row in data["by_disposition"]}
    assert data["totals"]["conversations"] == FULL
    assert data["totals"]["seeded"] == FULL
    assert dispositions["gift_request_approved"] > 0
    assert dispositions["gift_request_denied"] > 0
    assert dispositions["other"] > 0
    # Nothing was left out of the intent rows: the counts have to add up.
    assert sum(row["count"] for row in data["by_intent"]) == FULL
    assert sum(column["total"] for column in data["series"]) == FULL


async def test_the_history_is_spread_over_the_requested_range(client, use_fake_llm):
    use_fake_llm()
    summary = await seed(client, days=30, count=30)

    data = await analytics(client, days=30, interval="day")
    assert len(data["series"]) == 30
    # More than a handful of days have activity, or the chart would be one spike.
    assert len([row for row in data["series"] if row["total"] > 0]) > 10
    assert summary["range"]["to"] >= summary["range"]["from"]


async def test_each_sample_conversation_gets_its_own_session(client, use_fake_llm):
    """The duplicate guard keys on the session, so sharing one would turn every
    repeat order into a duplicate instead of a second gift."""
    use_fake_llm()
    summary = await seed(client, count=20)

    assert summary["sessions"] > 10
    assert summary["outcomes"]["gift_issued"] > summary["outcomes"].get("gift_duplicate", 0)


async def test_sample_turns_name_the_model_that_served_them(client, use_fake_llm):
    """A sample turn must be identifiable in the log, not just by its badge."""
    use_fake_llm()
    await seed(client, count=14)

    meta = (await client.get("/api/logs/meta")).json()
    sample = next(item for item in meta["conversations"] if item["source"] == "seeded")
    events = (
        await client.get(
            "/api/logs",
            params={"conversation_id": sample["id"], "scope": "session", "limit": 200},
        )
    ).json()["events"]

    assert events, "a sample conversation's events must be readable in the default scope"
    responses = [event for event in events if event["type"] == "llm_response"]
    assert responses
    assert all(event["payload"]["model"] == seeder.SIMULATED_MODEL_NAME for event in responses)
    # ...and it records which model was asked for, so nothing is passed off as real.
    assert all(event["payload"]["requested_model"] for event in responses)


async def test_generating_twice_replaces_rather_than_appends(client, use_fake_llm):
    use_fake_llm()
    first = await seed(client, count=LIGHT)
    second = await seed(client, count=LIGHT)

    assert second["cleared"]["conversations"] == first["conversations"]
    assert (await analytics(client))["totals"]["conversations"] == LIGHT


# ---------------------------------------------------------------------------
# Clearing
# ---------------------------------------------------------------------------
async def test_clearing_leaves_real_conversations_alone(client, use_fake_llm):
    use_fake_llm()
    live = await chat(client, "creator_001", "What products do you sell?")
    await seed(client, count=LIGHT)

    assert (await analytics(client))["totals"]["conversations"] == LIGHT + 1

    response = await client.delete("/api/admin/sample-history", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert response.json()["removed"]["conversations"] == LIGHT

    after = await analytics(client)
    assert after["totals"]["conversations"] == 1
    assert after["totals"]["seeded"] == 0

    # The visitor's own turn is untouched, events and all.
    events = (
        await client.get("/api/logs", params={"conversation_id": live["conversation_id"]})
    ).json()
    assert events["events"]


async def test_clearing_an_empty_database_is_harmless(client):
    response = await client.delete("/api/admin/sample-history", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert response.json()["removed"]["conversations"] == 0


# ---------------------------------------------------------------------------
# The plan itself
# ---------------------------------------------------------------------------
def test_the_plan_always_asks_for_exactly_what_was_requested():
    for count in (14, 20, 40, 60, 137, 400):
        assert len(seeder._plan(count)) == count


def test_the_plan_keeps_every_recipe_that_was_weighted_in():
    plan = seeder._plan(400)
    for recipe in seeder.RECIPES:
        assert recipe in plan, recipe.outcome


def test_the_plan_preserves_recipe_order_so_a_reorder_follows_its_order():
    """The duplicate recipe must run after the order it collides with."""
    keys = [(recipe.thread, recipe.outcome) for recipe in seeder._plan(FULL)]
    assert keys.index(("reorder", "gift_issued")) < keys.index(("reorder", "gift_duplicate"))


def test_the_shuffled_plan_spreads_outcomes_without_splitting_a_thread():
    """Times are handed out in execution order, so running the recipes as written
    would pile every approval at the start of the range and every refusal at the end.
    The shuffle breaks that up, but a thread still has to run in order."""
    plan = seeder._ordered(seeder._plan(FULL), random.Random(7))
    assert len(plan) == len(seeder._plan(FULL))

    approvals = [
        index for index, recipe in enumerate(plan) if recipe.outcome in {"gift_issued", "gift_duplicate"}
    ]
    third = len(plan) // 3
    # Every third of the plan gets a share of the approvals, and no single third
    # swallows most of them -- which is what running the recipes as written did.
    shares = [
        len([index for index in approvals if start <= index < start + third])
        for start in range(0, len(plan), third)
    ]
    assert all(share > 0 for share in shares), shares
    assert max(shares) < len(approvals) / 2, shares

    keys = [(recipe.thread, recipe.outcome) for recipe in plan]
    assert keys.index(("reorder", "gift_issued")) < keys.index(("reorder", "gift_duplicate"))


def test_start_times_cover_the_range_plus_the_rate_window_and_favour_recent_days():
    """The history starts before the range so the dashboard's trailing window has
    something to average over at the left edge of the chart."""
    starts = seeder._start_times(random.Random(7), count=200, days=30)
    today = datetime.now(timezone.utc).date()

    assert starts == sorted(starts)
    assert max(starts).date() <= today
    assert min(starts).date() >= today - timedelta(days=29 + seeder.SPAN_LOOKBACK_DAYS)
    # Weighted towards today, but only mildly: the newest fortnight should be busier
    # than the oldest without starving it.
    span = 29 + seeder.SPAN_LOOKBACK_DAYS
    recent = len([start for start in starts if (today - start.date()).days < 14])
    oldest = len([start for start in starts if (today - start.date()).days >= span - 14])
    assert recent > oldest
    assert oldest > 20


# ---------------------------------------------------------------------------
# Seeding at boot
# ---------------------------------------------------------------------------
async def test_boot_leaves_the_database_alone_unless_asked(monkeypatch):
    """Sample history is opt-in, so a local run or a test never gets it by surprise."""
    from app import bootstrap, settings

    monkeypatch.setattr(settings, "SEED_DEMO_HISTORY", False)
    async with AsyncSessionLocal() as db:
        await bootstrap.bootstrap(db)

    assert await _sample_count() == 0


async def test_boot_fills_an_empty_database_and_then_leaves_it_alone(monkeypatch):
    from app import bootstrap, settings

    monkeypatch.setattr(settings, "SEED_DEMO_HISTORY", True)
    monkeypatch.setattr(settings, "SEED_DEMO_COUNT", LIGHT)
    monkeypatch.setattr(settings, "SEED_DEMO_DAYS", 30)

    try:
        async with AsyncSessionLocal() as db:
            await bootstrap.bootstrap(db)
        assert await _sample_count() == LIGHT

        # A redeploy against a persistent database must not pile more on top.
        async with AsyncSessionLocal() as db:
            await bootstrap.bootstrap(db)
        assert await _sample_count() == LIGHT
    finally:
        # The database is shared across the session, so leave it as it was found.
        async with AsyncSessionLocal() as db:
            await seeder.clear_sample_history(db)


async def _sample_count() -> int:
    async with AsyncSessionLocal() as db:
        return int(
            await db.scalar(
                select(func.count(Conversation.id)).where(
                    Conversation.source == seeder.SAMPLE_SOURCE
                )
            )
            or 0
        )
