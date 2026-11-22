"""Live OpenRouter model catalogue (for the admin model pickers).

Falls back to a hard-coded shortlist if OpenRouter is unreachable, so the admin
UI always renders.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from .. import settings

CACHE_TTL_SECONDS = 3600

# Cheap, tool-capable models that are known-good for this demo.
RECOMMENDED = [
    "google/gemini-2.5-flash-lite",
    "openai/gpt-4.1-nano",
    "meta-llama/llama-3.1-8b-instruct",
    "mistralai/mistral-nemo",
    "openai/gpt-4o-mini",
    "google/gemini-2.5-flash",
    "anthropic/claude-3.5-haiku",
]

_FALLBACK: list[dict[str, Any]] = [
    {
        "id": "google/gemini-2.5-flash-lite",
        "name": "Google: Gemini 2.5 Flash Lite",
        "prompt_price_per_m": 0.10,
        "completion_price_per_m": 0.40,
        "context_length": 1048576,
        "supports_tools": True,
        "recommended": True,
    },
    {
        "id": "openai/gpt-4.1-nano",
        "name": "OpenAI: GPT-4.1 Nano",
        "prompt_price_per_m": 0.10,
        "completion_price_per_m": 0.40,
        "context_length": 1047576,
        "supports_tools": True,
        "recommended": True,
    },
    {
        "id": "meta-llama/llama-3.1-8b-instruct",
        "name": "Meta: Llama 3.1 8B Instruct",
        "prompt_price_per_m": 0.02,
        "completion_price_per_m": 0.04,
        "context_length": 131072,
        "supports_tools": True,
        "recommended": True,
    },
    {
        "id": "mistralai/mistral-nemo",
        "name": "Mistral: Nemo",
        "prompt_price_per_m": 0.019,
        "completion_price_per_m": 0.03,
        "context_length": 131072,
        "supports_tools": True,
        "recommended": True,
    },
]

_cache: dict[str, Any] = {"fetched_at": 0.0, "models": []}


def _per_million(value: Any) -> float | None:
    try:
        price = float(value) * 1_000_000
    except (TypeError, ValueError):
        return None
    # Dynamic-priced routers (e.g. openrouter/auto) report -1; they cannot be
    # compared on price and are not sensible per-agent choices.
    if price < 0:
        return None
    return round(price, 4)


async def list_models(force: bool = False) -> list[dict[str, Any]]:
    now = time.time()
    if not force and _cache["models"] and now - _cache["fetched_at"] < CACHE_TTL_SECONDS:
        return _cache["models"]

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{settings.OPENROUTER_BASE_URL}/models")
            response.raise_for_status()
            payload = response.json().get("data", [])
        models: list[dict[str, Any]] = []
        for item in payload:
            model_id = item.get("id", "")
            if not model_id or model_id.endswith(":free"):
                continue
            params = item.get("supported_parameters") or []
            if "tools" not in params:
                continue
            pricing = item.get("pricing") or {}
            prompt_price = _per_million(pricing.get("prompt"))
            completion_price = _per_million(pricing.get("completion"))
            if prompt_price is None or completion_price is None:
                continue
            models.append(
                {
                    "id": model_id,
                    "name": item.get("name") or model_id,
                    "prompt_price_per_m": prompt_price,
                    "completion_price_per_m": completion_price,
                    "context_length": item.get("context_length"),
                    "supports_tools": True,
                    "recommended": model_id in RECOMMENDED,
                }
            )
        models.sort(key=lambda m: (not m["recommended"], m["prompt_price_per_m"], m["id"]))
        if models:
            _cache.update(fetched_at=now, models=models)
            return models
    except Exception:  # noqa: BLE001 - network is best-effort
        pass

    return _FALLBACK
