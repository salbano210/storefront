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
    usage = data.get("usage")
    remaining = None
    if isinstance(limit, (int, float)) and isinstance(usage, (int, float)):
        remaining = round(float(limit) - float(usage), 4)

    return {
        "ok": True,
        "key_mode": "byok" if byok else "host",
        "label": data.get("label"),
        "is_free_tier": data.get("is_free_tier"),
        "limit_usd": limit,
        "usage_usd": usage,
        "remaining_usd": remaining,
        "message": (
            "Key works."
            if remaining is None or remaining > 0
            else "Key works, but the credit balance is zero -- paid models will fail. "
            "Add credits or use the host key."
        ),
    }
