"""Health, model catalogue and BYOK key validation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import settings
from ..db import get_db
from ..services import config_manager, model_catalog
from .deps import resolve_api_key

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)) -> dict:
    db_ok = True
    try:
        await db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - health checks must not raise
        db_ok = False
    try:
        version, _ = await config_manager.get_current(db)
    except Exception:  # noqa: BLE001
        version = None
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "error",
        "config_version": version,
        "host_key_configured": settings.HOST_KEY_AVAILABLE,
        "admin_protected": bool(settings.ADMIN_PASSCODE),
        "models": settings.DEFAULT_MODELS,
    }


@router.get("/models")
async def models(refresh: bool = False) -> dict:
    catalog = await model_catalog.list_models(force=refresh)
    return {"models": catalog, "recommended": model_catalog.RECOMMENDED}


@router.post("/key/test")
async def test_key(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    key, byok = resolve_api_key(request)
    import httpx

    headers = {"Authorization": f"Bearer {key}"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(f"{settings.OPENROUTER_BASE_URL}/key", headers=headers)
    except Exception as exc:  # noqa: BLE001 - network failure is a normal outcome
        raise HTTPException(
            status_code=502,
            detail={"code": "network_error", "message": f"Could not reach OpenRouter: {exc}"},
        ) from exc

    if response.status_code == 401:
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_key", "message": "OpenRouter rejected that key."},
        )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "openrouter_error",
                "message": f"OpenRouter returned {response.status_code}.",
            },
        )

    data = (response.json() or {}).get("data") or {}
    limit = data.get("limit")
    remaining = data.get("limit_remaining")
    usage = data.get("usage")
    free_models = data.get("free_model_daily_requests") or None

    facts: list[str] = []
    if isinstance(remaining, (int, float)):
        facts.append(f"${float(remaining):.2f} left on this key's credit limit")
    if isinstance(usage, (int, float)):
        facts.append(f"${float(usage):.2f} spent on this key so far")

    if isinstance(remaining, (int, float)) and float(remaining) <= 0:
        message = (
            "This key is valid but its credit limit is exhausted -- paid models will fail. "
            "Raise the limit in OpenRouter or use the demo key."
        )
    else:
        message = "Key works" + (f" — {', '.join(facts)}." if facts else ".")

    return {
        "ok": True,
        "key_mode": "byok" if byok else "host",
        "label": data.get("label"),
        "is_free_tier": data.get("is_free_tier"),
        "limit_usd": limit,
        "limit_remaining_usd": remaining,
        "usage_usd": usage,
        "usage_daily_usd": data.get("usage_daily"),
        "free_model_requests": free_models,
        "message": message,
    }
