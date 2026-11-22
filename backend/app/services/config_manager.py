"""Brand configuration: load defaults, version, persist, validate.

The repo JSON file is the *default*. The live configuration lives in the
``config_versions`` table, so admin edits persist and every execution can be
stamped with the version that was in force.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import settings
from ..models import ConfigVersion
from ..schemas import BrandConfig
from . import data_store

DEFAULT_CONFIG_PATH = data_store.DATA_DIR / "brand_config.default.json"


class ConfigError(Exception):
    """Raised when a configuration cannot be loaded or validated."""


class ConfigConflictError(ConfigError):
    """Raised when a save is attempted against a stale version."""


def load_default_config() -> BrandConfig:
    """Read the repo default config, seeding models + products from env/data."""
    try:
        with DEFAULT_CONFIG_PATH.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - corrupt repo file
        raise ConfigError(f"cannot read default brand config: {exc}") from exc

    # The product catalog fixture is the source of truth for the default SKUs.
    products = data_store.list_products()
    if products:
        raw.setdefault("gifting", {})["eligible_products"] = products

    # Env vars win over the checked-in defaults so deployment config is explicit.
    raw.setdefault("models", {}).update(
        {key: value for key, value in settings.DEFAULT_MODELS.items() if value}
    )
    return BrandConfig(**raw)


def validate_config_payload(payload: dict) -> BrandConfig:
    try:
        return BrandConfig(**payload)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc


async def ensure_seeded(db: AsyncSession) -> tuple[int, BrandConfig]:
    """Create version 1 from the repo default if the table is empty.

    Also self-heals: if the newest stored configuration no longer validates
    against the current schema (e.g. the schema changed between deploys), a
    fresh version is seeded from the repo defaults rather than crashing every
    request.
    """
    existing = await db.scalar(select(ConfigVersion).order_by(desc(ConfigVersion.version)).limit(1))
    if existing:
        try:
            return existing.version, BrandConfig(**json.loads(existing.config_json))
        except (ValidationError, json.JSONDecodeError) as exc:
            print(
                f"[config] stored v{existing.version} no longer validates ({exc}); "
                "seeding a new version from repo defaults"
            )
            config = load_default_config()
            row = ConfigVersion(
                config_json=config.model_dump_json(),
                updated_by=f"reseed-after-schema-change (was v{existing.version})",
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return row.version, config
    config = load_default_config()
    row = ConfigVersion(config_json=config.model_dump_json(), updated_by="seed")
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row.version, config


async def get_current(db: AsyncSession) -> tuple[int, BrandConfig]:
    return await ensure_seeded(db)


async def save_config(
    db: AsyncSession,
    config: BrandConfig,
    *,
    updated_by: str | None = None,
    expected_version: int | None = None,
) -> tuple[int, BrandConfig]:
    current_version, _ = await ensure_seeded(db)
    if expected_version is not None and expected_version != current_version:
        raise ConfigConflictError(
            f"configuration changed while you were editing "
            f"(expected v{expected_version}, current v{current_version})"
        )
    row = ConfigVersion(config_json=config.model_dump_json(), updated_by=updated_by or "admin")
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row.version, config


async def reset_to_default(
    db: AsyncSession, *, updated_by: str | None = None
) -> tuple[int, BrandConfig]:
    return await save_config(db, load_default_config(), updated_by=updated_by or "reset")


def public_schema() -> dict:
    """JSON schema for the raw-JSON editor in the admin UI."""
    return BrandConfig.model_json_schema()
