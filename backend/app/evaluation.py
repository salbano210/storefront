"""Deterministic business rules.

This module is deliberately boring and contains no LLM calls. The agents gather
information and talk to the creator; *these* functions decide. That separation is
what makes the demo trustworthy: a cheap model can never approve an ineligible
creator, and lowering a threshold in the admin UI provably changes outcomes.
"""

from __future__ import annotations

import re
from typing import Any

from .schemas import BrandConfig, CreatorProfile


def fmt_int(value: float | int) -> str:
    return f"{int(value):,}"


def fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def fmt_money(value: float) -> str:
    return f"${value:,.0f}"


# ---------------------------------------------------------------------------
# Gifting eligibility
# ---------------------------------------------------------------------------
def evaluate_gifting(creator: CreatorProfile, config: BrandConfig) -> dict[str, Any]:
    """Compare verified creator data against the configured gifting criteria."""
    criteria = config.gifting.criteria
    checks: list[dict[str, Any]] = []

    checks.append(
        {
            "criterion": "minimum_followers",
            "label": "Follower count",
            "required": criteria.minimum_followers,
            "required_display": fmt_int(criteria.minimum_followers),
            "actual": creator.followers,
            "actual_display": fmt_int(creator.followers),
            "passed": creator.followers >= criteria.minimum_followers,
            "source": "verified profile",
        }
    )
    checks.append(
        {
            "criterion": "minimum_engagement_rate",
            "label": "Engagement rate",
            "required": criteria.minimum_engagement_rate,
            "required_display": fmt_pct(criteria.minimum_engagement_rate),
            "actual": creator.engagement_rate,
            "actual_display": fmt_pct(creator.engagement_rate),
            "passed": creator.engagement_rate >= criteria.minimum_engagement_rate - 1e-9,
            "source": "verified profile",
        }
    )
    checks.append(
        {
            "criterion": "minimum_brand_revenue",
            "label": "Brand-attributed revenue",
            "required": criteria.minimum_brand_revenue,
            "required_display": fmt_money(criteria.minimum_brand_revenue),
            "actual": creator.brand_attributed_revenue,
            "actual_display": fmt_money(creator.brand_attributed_revenue),
            "passed": creator.brand_attributed_revenue >= criteria.minimum_brand_revenue,
            "source": "verified profile",
        }
    )

    if criteria.required_categories:
        wanted = {c.lower() for c in criteria.required_categories}
        actual = {c.lower() for c in creator.categories}
        checks.append(
            {
                "criterion": "required_categories",
                "label": "Creator category",
                "required": criteria.required_categories,
                "required_display": ", ".join(criteria.required_categories),
                "actual": creator.categories,
                "actual_display": ", ".join(creator.categories),
                "passed": bool(wanted & actual),
                "source": "verified profile",
            }
        )

    failed = [check for check in checks if not check["passed"]]
    return {
        "eligible": not failed,
        "checks": checks,
        "failed": [check["criterion"] for check in failed],
        "failed_labels": [check["label"] for check in failed],
        "creator_id": creator.id,
        "verified": {
            "followers": creator.followers,
            "engagement_rate": creator.engagement_rate,
            "brand_attributed_revenue": creator.brand_attributed_revenue,
            "categories": creator.categories,
        },
    }


# ---------------------------------------------------------------------------
# Pitch fit scoring
# ---------------------------------------------------------------------------
_AGE_RE = re.compile(r"(\d{1,2})\s*[-\u2013]\s*(\d{1,2})")


def _age_range(value: str) -> tuple[int, int] | None:
    match = _AGE_RE.search(value or "")
    if not match:
        return None
    low, high = int(match.group(1)), int(match.group(2))
    return (min(low, high), max(low, high))


def _age_overlap(preferred: str, actual: str) -> bool:
    wanted = _age_range(preferred)
    have = _age_range(actual)
    if not wanted or not have:
        return True  # unknown -> do not penalise
    return not (have[1] < wanted[0] or have[0] > wanted[1])


def _ratio(actual: float, ideal: float) -> float:
    if ideal <= 0:
        return 0.0
    return max(0.0, min(1.0, actual / ideal))


def _component(key: str, label: str, ratio: float, weight: float, detail: str) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "ratio": round(ratio, 4),
        "weight": weight,
        "points": round(ratio * weight, 2),
        "detail": detail,
    }


def pitch_evaluation(creator: CreatorProfile, config: BrandConfig) -> dict[str, Any]:
    """Score a creator against the brand's ideal profile and pick a label."""
    criteria = config.pitch.criteria
    targets = config.pitch.targets
    weights = config.pitch.weights

    preferred = {c.lower() for c in criteria.preferred_categories}
    actual_categories = {c.lower() for c in creator.categories}
    overlap = sorted(preferred & actual_categories)
    category_ratio = 1.0 if overlap else 0.0

    components = [
        _component(
            "category",
            "Category fit",
            category_ratio,
            weights["category"],
            f"matches {', '.join(overlap)}" if overlap else "no overlap with preferred categories",
        ),
        _component(
            "engagement",
            "Engagement rate",
            _ratio(creator.engagement_rate, targets.ideal_engagement_rate),
            weights["engagement"],
            f"{fmt_pct(creator.engagement_rate)} vs ideal {fmt_pct(targets.ideal_engagement_rate)}",
        ),
        _component(
            "followers",
            "Audience size",
            _ratio(creator.followers, targets.ideal_followers),
            weights["followers"],
            f"{fmt_int(creator.followers)} vs ideal {fmt_int(targets.ideal_followers)}",
        ),
        _component(
            "brand_revenue",
            "Brand-attributed revenue",
            _ratio(creator.brand_attributed_revenue, targets.ideal_brand_revenue),
            weights["brand_revenue"],
            f"{fmt_money(creator.brand_attributed_revenue)} vs ideal {fmt_money(targets.ideal_brand_revenue)}",
        ),
        _component(
            "shopmy_sales",
            "Historical ShopMy sales",
            _ratio(creator.shopmy_sales, targets.ideal_shopmy_sales),
            weights["shopmy_sales"],
            f"{fmt_money(creator.shopmy_sales)} vs ideal {fmt_money(targets.ideal_shopmy_sales)}",
        ),
    ]

    score = round(sum(component["points"] for component in components), 2)
    label = label_for_score(score, config)

    strengths = [f"{c['label']}: {c['detail']}" for c in components if c["ratio"] >= 0.8]
    concerns = [f"{c['label']}: {c['detail']}" for c in components if c["ratio"] < 0.4]

    minimum_failures: list[str] = []
    if creator.followers < criteria.minimum_followers:
        minimum_failures.append(f"below the {fmt_int(criteria.minimum_followers)} follower minimum")
    if creator.engagement_rate < criteria.minimum_engagement_rate - 1e-9:
        minimum_failures.append(
            f"below the {fmt_pct(criteria.minimum_engagement_rate)} engagement minimum"
        )
    if creator.brand_attributed_revenue < criteria.minimum_brand_revenue:
        minimum_failures.append(
            f"below the {fmt_money(criteria.minimum_brand_revenue)} revenue minimum"
        )
    if not _age_overlap(criteria.preferred_audience_age, creator.primary_age_range):
        concerns.append(
            f"Audience age {creator.primary_age_range} sits outside the preferred "
            f"{criteria.preferred_audience_age}"
        )

    return {
        "score": score,
        "label": label,
        "labels": [item.label for item in config.pitch.classification_labels],
        "components": components,
        "strengths": strengths,
        "concerns": concerns + minimum_failures,
        "minimums_met": not minimum_failures,
        "minimum_failures": minimum_failures,
        "creator_id": creator.id,
        "verified": {
            "followers": creator.followers,
            "engagement_rate": creator.engagement_rate,
            "categories": creator.categories,
            "brand_attributed_revenue": creator.brand_attributed_revenue,
            "shopmy_sales": creator.shopmy_sales,
            "primary_age_range": creator.primary_age_range,
        },
    }


def label_for_score(score: float, config: BrandConfig) -> str:
    """Highest configured label whose threshold the score reaches."""
    for item in config.pitch.classification_labels:
        if score >= item.min_score:
            return item.label
    return config.pitch.classification_labels[-1].label


def find_product(config: BrandConfig, sku: str) -> Any | None:
    wanted = (sku or "").strip().upper()
    for product in config.gifting.eligible_products:
        if product.sku == wanted:
            return product
    return None


def eligible_product(config: BrandConfig, sku: str) -> Any | None:
    product = find_product(config, sku)
    return product if product is not None and product.eligible else None

