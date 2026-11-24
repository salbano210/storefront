"""Brand configuration: validation, versioning and persistence."""

from __future__ import annotations

import copy
import json

import pytest
from conftest import ADMIN_HEADERS

from app.db import AsyncSessionLocal
from app.models import ConfigVersion
from app.schemas import CONFIG_SCHEMA_VERSION
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
    assert current["defaults"]["gifting"]["offers"][0]["sku"] == "GT-SERUM-001"

    config = current["config"]
    config["gifting"]["offers"][0]["criteria"][0]["value"] = 75000
    updated = await client.put(
        "/api/config",
        json={"config": config, "expected_version": current["version"], "updated_by": "tester"},
        headers=ADMIN_HEADERS,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == current["version"] + 1

    after = await _config(client)
    assert after["config"]["gifting"]["offers"][0]["criteria"][0]["value"] == 75000

    reset = await client.post("/api/config/reset", headers=ADMIN_HEADERS)
    assert reset.status_code == 200
    final = await _config(client)
    assert final["config"]["gifting"]["offers"][0]["criteria"][0]["value"] == 50000


async def test_update_requires_passcode(client):
    current = await _config(client)
    response = await client.put(
        "/api/config", json={"config": current["config"], "expected_version": current["version"]}
    )
    assert response.status_code == 401


async def test_invalid_values_are_rejected(client):
    current = await _config(client)

    async def rejected(mutate) -> int:
        payload = copy.deepcopy(current["config"])
        mutate(payload)
        response = await client.put(
            "/api/config",
            json={"config": payload, "expected_version": current["version"]},
            headers=ADMIN_HEADERS,
        )
        return response.status_code

    def _bad_percent(config):
        # Percent metrics are stored as a fraction: 5.0 is a units mistake.
        config["gifting"]["offers"][0]["criteria"][1]["value"] = 5.0

    def _duplicate_sku(config):
        config["gifting"]["offers"][1]["sku"] = config["gifting"]["offers"][0]["sku"]

    def _duplicate_gift_id(config):
        config["gifting"]["offers"][1]["id"] = config["gifting"]["offers"][0]["id"]

    def _no_offers(config):
        config["gifting"]["offers"] = []

    def _unknown_metric(config):
        config["gifting"]["offers"][0]["criteria"][0]["metric"] = "shoe_size"

    def _wrong_operator(config):
        # 'gte' is meaningless for a list metric.
        config["gifting"]["offers"][2]["criteria"][1]["operator"] = "gte"

    def _bad_url(config):
        config["gifting"]["offers"][0]["url"] = "summersoft.example/serum"

    assert await rejected(_bad_percent) == 422
    assert await rejected(_duplicate_sku) == 422
    assert await rejected(_duplicate_gift_id) == 422
    assert await rejected(_no_offers) == 422
    assert await rejected(_unknown_metric) == 422
    assert await rejected(_wrong_operator) == 422
    assert await rejected(_bad_url) == 422


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
        assert config.gifting.offers[0].sku == "GT-SERUM-001"
        assert version > 1

    # the healed version is what subsequent requests see
    async with AsyncSessionLocal() as db:
        version_again, config_again = await config_manager.get_current(db)
        assert version_again == version
        assert config_again.brand.name == "Summer Soft Skincare"


async def test_a_config_from_an_older_schema_version_is_reseeded():
    """A stored config that still parses, but predates what this build ships.

    Added blocks are the case: the old config validates, so nothing fails loudly --
    it would simply never gain the new rules. Reseeding is the honest option, since
    a stored config is a copy of the defaults plus admin edits and there is no way
    to tell which edits matter.
    """
    payload = json.loads(config_manager.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    payload["schema_version"] = CONFIG_SCHEMA_VERSION - 1
    async with AsyncSessionLocal() as db:
        db.add(
            ConfigVersion(config_json=json.dumps(payload), updated_by="older-schema")
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        version, config = await config_manager.ensure_seeded(db)
        assert config.schema_version == CONFIG_SCHEMA_VERSION
        assert version > 1
