"""Brand configuration endpoints (admin-gated)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from .. import metrics
from ..db import get_db
from ..schemas import ConfigUpdateRequest
from ..services import config_manager, event_logger as events
from ..services.event_logger import EventLogger
from .deps import require_admin, session_id

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
async def read_config(
    db: AsyncSession = Depends(get_db), _: str = Depends(require_admin)
) -> dict:
    version, config = await config_manager.get_current(db)
    return {
        "version": version,
        "config": config.model_dump(),
        "defaults": config_manager.load_default_config().model_dump(),
    }


@router.get("/schema")
async def read_schema(_: str = Depends(require_admin)) -> dict:
    return config_manager.public_schema()


@router.get("/metrics")
async def read_metrics(_: str = Depends(require_admin)) -> dict:
    """The verified-metric registry -- this is what fills the admin dropdowns.

    Every criterion and every scoring input is drawn from this list, so the UI
    never hard-codes a metric name.
    """
    return {
        "metrics": metrics.metric_catalog(),
        "operators": [
            {"value": key, "label": label} for key, label in metrics.OPERATOR_LABELS.items()
        ],
    }


@router.put("")
async def update_config(
    payload: ConfigUpdateRequest,
    db: AsyncSession = Depends(get_db),
    sid: str = Depends(session_id),
    _: str = Depends(require_admin),
) -> dict:
    try:
        version, config = await config_manager.save_config(
            db,
            payload.config,
            updated_by=payload.updated_by,
            expected_version=payload.expected_version,
        )
    except config_manager.ConfigConflictError as exc:
        raise HTTPException(
            status_code=409, detail={"code": "config_conflict", "message": str(exc)}
        ) from exc
    except config_manager.ConfigError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_config", "message": str(exc)}
        ) from exc

    await EventLogger(db, session_id=sid, config_version=version).log(
        events.CONFIG_UPDATED,
        agent="admin",
        summary=f"Brand configuration updated to v{version}",
        payload={
            "version": version,
            "updated_by": payload.updated_by or "admin",
            "gift_offers": [
                {
                    "id": offer.id,
                    "sku": offer.sku,
                    "name": offer.name,
                    "retail_price": offer.retail_price,
                    "criteria": [rule.describe() for rule in offer.criteria],
                }
                for offer in config.gifting.offers
            ],
            "models": config.models.model_dump(),
        },
    )
    return {"version": version, "config": config.model_dump()}


@router.post("/reset")
async def reset_config(
    db: AsyncSession = Depends(get_db),
    sid: str = Depends(session_id),
    _: str = Depends(require_admin),
) -> dict:
    version, config = await config_manager.reset_to_default(db, updated_by="admin-reset")
    await EventLogger(db, session_id=sid, config_version=version).log(
        events.CONFIG_RESET,
        agent="admin",
        summary=f"Brand configuration reset to repo defaults (v{version})",
        payload={"version": version},
    )
    return {"version": version, "config": config.model_dump()}
