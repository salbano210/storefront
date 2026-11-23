"""The analytics tab: conversation volume by intent, and disposition counts/rates.

Every number the dashboard shows is asserted here against conversations created
through the real chat API, so the counts can't drift from what the agents did.
"""

from __future__ import annotations

import pytest
from conftest import ADMIN_HEADERS

from app.db import AsyncSessionLocal
from app.models import Conversation

pytestmark = pytest.mark.asyncio


async def chat(client, creator_id: str, message: str, conversation_id: str | None = None):
    body = {"creator_id": creator_id, "message": message}
    if conversation_id:
        body["conversation_id"] = conversation_id
    response = await client.post("/api/chat", json=body)
    assert response.status_code == 200, response.text
    return response.json()


async def analytics(client, *, headers=None, **params) -> dict:
    response = await client.get("/api/analytics", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def approve_a_gift(client, creator_id: str) -> dict:
    """Ask for a gift and settle the address, so the turn ends in an order."""
    opening = await chat(client, creator_id, "I'd love the Barrier Repair Serum")
    if opening["outcome"]["type"] == "details_required":
        return await chat(
            client, creator_id, "Yes, that address is right.", opening["conversation_id"]
        )
    return opening


def by_key(rows: list[dict], key: str) -> dict:
    return {row[key]: row for row in rows}


# ---------------------------------------------------------------------------
# Conversation count by intent
# ---------------------------------------------------------------------------
async def test_conversations_are_counted_by_intent(client, use_fake_llm):
    use_fake_llm()
    await approve_a_gift(client, "creator_001")
    await chat(client, "creator_002", "What products do you sell?")
    await chat(client, "creator_003", "Can I get the Hydrating Cleanser?")

    data = await analytics(client, days=7, interval="day")
    intents = by_key(data["by_intent"], "intent")

    assert data["totals"]["conversations"] == 3
    assert intents["gift_request"]["count"] == 2
    assert intents["general_inquiry"]["count"] == 1
    # The dashboard reads its names off the payload, not off hard-coded strings.
    assert intents["gift_request"]["label"] == "Gift request"


async def test_intent_rates_are_shares_of_the_total(client, use_fake_llm):
    use_fake_llm()
    await approve_a_gift(client, "creator_001")
    await chat(client, "creator_002", "What products do you sell?")
    await chat(client, "creator_003", "What's your shipping policy?")

    data = await analytics(client)
    intents = by_key(data["by_intent"], "intent")

    assert intents["gift_request"]["rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert intents["general_inquiry"]["rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert sum(row["rate"] for row in data["by_intent"]) == pytest.approx(1.0, abs=1e-4)


async def test_a_follow_up_turn_does_not_create_a_second_conversation(client, use_fake_llm):
    """Intent counts conversations, so resuming one must not inflate the total."""
    use_fake_llm()
    await approve_a_gift(client, "creator_001")

    data = await analytics(client)
    assert data["totals"]["conversations"] == 1


async def test_a_legacy_intent_is_still_counted(client, use_fake_llm):
    """Rows written before the intents changed must not vanish from the totals.

    A conversation stored with an intent we no longer classify has to appear in
    by_intent (and therefore in the chart), or the counts wouldn't add up.
    """
    use_fake_llm()
    await approve_a_gift(client, "creator_001")
    # Attach the legacy row to this browser's own session, so the default
    # (session-scoped) view has to account for it.
    session_id = client.cookies.get("storefront_session")

    async with AsyncSessionLocal() as db:
        db.add(
            Conversation(
                id="conv_legacy",
                session_id=session_id,
                creator_id="creator_002",
                intent="creator_pitch",
                disposition="gift_request_approved",
                last_outcome="gift_issued",
            )
        )
        await db.commit()

    data = await analytics(client)
    intents = by_key(data["by_intent"], "intent")

    assert data["totals"]["conversations"] == 2
    assert intents["creator_pitch"]["count"] == 1
    assert intents["creator_pitch"]["label"] == "Creator pitch"
    assert sum(row["count"] for row in data["by_intent"]) == 2
    # And it is drawn, not silently dropped from the stack.
    assert sum(column["total"] for column in data["series"]) == 2


# ---------------------------------------------------------------------------
# Disposition count/rate
# ---------------------------------------------------------------------------
async def test_dispositions_count_approvals_denials_and_other(client, use_fake_llm):
    use_fake_llm()
    await approve_a_gift(client, "creator_001")
    # creator_004 does not meet the serum's criteria.
    denied = await chat(client, "creator_004", "Can I get the Barrier Repair Serum?")
    assert denied["outcome"]["type"] == "gift_declined"
    await chat(client, "creator_002", "What products do you sell?")

    data = await analytics(client)
    dispositions = by_key(data["by_disposition"], "disposition")

    assert dispositions["gift_request_approved"]["count"] == 1
    assert dispositions["gift_request_denied"]["count"] == 1
    assert dispositions["other"]["count"] == 1
    assert dispositions["gift_request_approved"]["label"] == "Gift request approved"
    assert sum(row["rate"] for row in data["by_disposition"]) == pytest.approx(1.0, abs=1e-4)


async def test_every_disposition_is_reported_even_at_zero(client, use_fake_llm):
    """The dashboard shows a 0% row rather than silently dropping a bucket."""
    use_fake_llm()
    await chat(client, "creator_002", "What products do you sell?")

    data = await analytics(client)
    dispositions = by_key(data["by_disposition"], "disposition")

    assert set(dispositions) == {
        "gift_request_approved",
        "gift_request_denied",
        "other",
        "escalated",
    }
    assert dispositions["gift_request_approved"]["count"] == 0
    assert dispositions["gift_request_approved"]["rate"] == 0.0
    # Escalation is its own outcome, not a kind of "other".
    assert dispositions["escalated"]["count"] == 0
    assert dispositions["escalated"]["label"] == "Escalated to a human"


async def test_the_other_bucket_is_broken_down_by_outcome(client, use_fake_llm):
    """'Other' is a bucket, not a black box: the raw outcome is reported too."""
    use_fake_llm()
    await chat(client, "creator_002", "What products do you sell?")
    # Qualifies, but the address has not been settled, so nothing was ordered.
    pending = await chat(client, "creator_001", "I'd love the Barrier Repair Serum")
    assert pending["outcome"]["type"] == "details_required"

    data = await analytics(client)
    outcomes = by_key(data["by_outcome"], "outcome")

    assert outcomes["inquiry"]["count"] == 1
    assert outcomes["details_required"]["count"] == 1
    assert outcomes["details_required"]["label"] == "Awaiting details"


# ---------------------------------------------------------------------------
# Timeseries
# ---------------------------------------------------------------------------
async def test_series_has_one_bucket_per_day_and_no_gaps(client, use_fake_llm):
    use_fake_llm()
    await approve_a_gift(client, "creator_001")

    data = await analytics(client, days=7, interval="day")
    series = data["series"]

    assert len(series) == 7
    assert [row["bucket"] for row in series] == sorted(row["bucket"] for row in series)
    # Today's bucket carries the conversation; the six quiet days are still drawn.
    assert series[-1]["total"] == 1
    assert series[-1]["counts"]["gift_request"] == 1
    assert all(row["total"] == 0 for row in series[:-1])


async def test_series_collapses_to_one_bucket_for_week_and_month(client, use_fake_llm):
    use_fake_llm()
    await approve_a_gift(client, "creator_001")

    weekly = await analytics(client, days=7, interval="week")
    monthly = await analytics(client, days=7, interval="month")

    # Weekly buckets align to Mondays, and monthly ones to the 1st, so a 7-day
    # window touches two buckets whenever it straddles one of those boundaries.
    assert 1 <= len(weekly["series"]) <= 2, [row["bucket"] for row in weekly["series"]]
    assert sum(row["total"] for row in weekly["series"]) == 1
    assert all(row["label"].startswith("wk ") for row in weekly["series"])

    assert 1 <= len(monthly["series"]) <= 2, [row["bucket"] for row in monthly["series"]]
    assert sum(row["total"] for row in monthly["series"]) == 1
    assert all(len(row["label"].split()) == 2 for row in monthly["series"])  # "Nov 2026"


async def test_the_range_is_reported_back_for_the_selector(client, use_fake_llm):
    use_fake_llm()
    await approve_a_gift(client, "creator_001")

    data = await analytics(client, days=90, interval="week")

    assert data["range"]["days"] == 90
    assert data["range"]["interval"] == "week"
    assert data["range"]["from"] < data["range"]["to"]
    assert data["totals"]["conversations"] == 1


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------
async def test_all_sessions_scope_requires_the_admin_passcode(client, use_fake_llm):
    use_fake_llm()
    await approve_a_gift(client, "creator_001")

    denied = await client.get("/api/analytics", params={"scope": "all"})
    assert denied.status_code == 401
    assert denied.json()["detail"]["code"] == "admin_passcode_required"

    allowed = await client.get(
        "/api/analytics", params={"scope": "all"}, headers=ADMIN_HEADERS
    )
    assert allowed.status_code == 200
    assert allowed.json()["scope"] == "all"
    assert allowed.json()["totals"]["conversations"] >= 1


async def test_an_empty_session_reports_zeroes_not_an_error(client):
    data = await analytics(client, days=30, interval="day")

    assert data["totals"]["conversations"] == 0
    assert len(data["series"]) == 30
    assert all(row["total"] == 0 for row in data["series"])
    assert all(row["rate"] == 0.0 for row in data["by_intent"])
    assert all(row["rate"] == 0.0 for row in data["by_disposition"])
