"""Mock Storefront backend data: creator profiles and the product catalog.

This module is the only place that reads the JSON fixtures. Swapping these for
real API calls later means changing this file alone.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..schemas import CreatorCard, CreatorProfile

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CREATORS_PATH = DATA_DIR / "creators.json"
PRODUCTS_PATH = DATA_DIR / "products.json"


@lru_cache(maxsize=8)
def _load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=1)
def _profiles() -> dict[str, CreatorProfile]:
    raw = _load(CREATORS_PATH).get("creators", [])
    return {item["id"]: CreatorProfile(**item) for item in raw}


def list_creator_cards() -> list[CreatorCard]:
    """Public card data for the creator-selection screen."""
    return [CreatorCard(**profile.model_dump()) for profile in _profiles().values()]


def get_creator(creator_id: str) -> CreatorProfile | None:
    return _profiles().get(creator_id)


def list_products() -> list[dict[str, Any]]:
    """Full catalog, including SKUs that are not giftable."""
    return list(_load(PRODUCTS_PATH).get("products", []))


def reload() -> None:
    """Test helper -- drop the caches."""
    _load.cache_clear()
    _profiles.cache_clear()
