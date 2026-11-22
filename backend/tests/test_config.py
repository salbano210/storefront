"""Brand configuration: validation, versioning and persistence."""

from __future__ import annotations

import json

import pytest
from conftest import ADMIN_HEADERS

from app.db import AsyncSessionLocal
from app.models import ConfigVersion
from app.services import config_manager

pytestmark = pytest.mark.asyncio


async def _config(client) -> dict:
    response = await client.get("/api/config", headers=ADMIN_HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


async def test_config_requires_passcode(client):
    assert (await client.get("/api/config")).status_code == 401
    wrong = await client.get("/api/config", headers={"X-Admin-Passcode": "nope"})
    assert wrong.status_code == 401
    assert wrong.json()["detail"]["code"] == "admin_passcode_required"


async def test_read_update_and_reset(client):
    current = await _config(client)
    assert current["version"] >= 1
    assert current["config"]["brand"]["name"] == "Summer Soft Skincare"
    assert current["defaults"]["gifting"]["criteria"]["minimum_followers"] == 10000

    config = current["config"]
    config["gifting"]["criteria"]["minimum_followers"] = 25000
    updated = await client.put(
        "/api/config",
        json={"config": config, "expected_version": current["version"], "updated_by": "tester"},
        headers=ADMIN_HEADERS,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == current["version"] + 1

    after = await _config(client)
    assert after["config"]["gifting"]["criteria"]["minimum_followers"] == 25000

    reset = await client.post("/api/config/reset", headers=ADMIN_HEADERS)
    assert reset.status_code == 200
    final = await _config(client)
    assert final["config"]["gifting"]["criteria"]["minimum_followers"] == 10000


async def test_update_requires_passcode(client):
    current = await _config(client)
    response = await client.put(
        "/api/config", json={"config": current["config"], "expected_version": current["version"]}
    )
    assert response.status_code == 401


async def test_invalid_values_are_rejected(client):
    current = await _config(client)

    bad_rate = current["config"]
    bad_rate["gifting"]["criteria"]["minimum_engagement_rate"] = 5.0
    assert (
        await client.put(
            "/api/config",
            json={"config": bad_rate, "expected_version": current["version"]},
            headers=ADMIN_HEADERS,
        )
    ).status_code == 422

    duplicate_sku = current["config"]
    product = duplicate_sku["gifting"]["eligible_products"][0]
    duplicate_sku["gifting"]["eligible_products"] = [product, dict(product)]
    assert (
        await client.put(
            "/api/config",
            json={"config": duplicate_sku, "expected_version": current["version"]},
            headers=ADMIN_HEADERS,
        )
    ).status_code == 422

    no_labels = current["config"]
    no_labels["pitch"]["classification_labels"] = []
    assert (
        await client.put(
            "/api/config",
            json={"config": no_labels, "expected_version": current["version"]},
            headers=ADMIN_HEADERS,
        )
    ).status_code == 422

    bad_weights = current["config"]
    bad_weights["pitch"]["weights"]["category"] = -10
    bad_weights["pitch"]["weights"]["engagement"] = 0
    bad_weights["pitch"]["weights"]["followers"] = 0
    bad_weights["pitch"]["weights"]["brand_revenue"] = 0
    bad_weights["pitch"]["weights"]["storefront_sales"] = 0
    assert (
        await client.put(
            "/api/config",
            json={"config": bad_weights, "expected_version": current["version"]},
            headers=ADMIN_HEADERS,
        )
    ).status_code == 422


async def test_stale_version_conflicts(client):
    current = await _config(client)
    response = await client.put(
        "/api/config",
        json={"config": current["config"], "expected_version": current["version"] + 99},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "config_conflict"


async def test_config_changes_are_audited(client):
    current = await _config(client)
    config = current["config"]
    config["brand"]["tone"] = "crisp and technical"
    await client.put(
        "/api/config",
        json={"config": config, "expected_version": current["version"], "updated_by": "auditor"},
        headers=ADMIN_HEADERS,
    )
    logs = (await client.get("/api/logs", params={"event_type": "config_updated"})).json()
    assert logs["count"] >= 1
    event = logs["events"][-1]
    assert event["agent"] == "admin"
    assert event["payload"]["updated_by"] == "auditor"
    assert event["config_version"] >= current["version"] + 1

    await client.post("/api/config/reset", headers=ADMIN_HEADERS)


async def test_schema_endpoint(client):
    response = await client.get("/api/config/schema", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    schema = response.json()
    assert "properties" in schema
    assert "gifting" in schema["properties"]


async def test_stored_config_that_no_longer_validates_is_reseeded():
    """A schema change between deploys must not brick every request."""
    async with AsyncSessionLocal() as db:
        db.add(
            ConfigVersion(
                config_json=json.dumps({"brand": {"name": "Legacy"}, "shopmy_sales": 1}),
                updated_by="legacy-schema",
            )
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        version, config = await config_manager.ensure_seeded(db)
        assert config.brand.name == "Summer Soft Skincare"
        assert config.pitch.weights["storefront_sales"] == 10
        assert version > 1

    # the healed version is what subsequent requests see
    async with AsyncSessionLocal() as db:
        version_again, config_again = await config_manager.get_current(db)
        assert version_again == version
        assert config_again.brand.name == "Summer Soft Skincare"
