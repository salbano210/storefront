"""Deterministic business rules.

This module is deliberately boring and contains no LLM calls. The agents gather
information and talk to the creator; *these* functions decide. That separation is
what makes the demo trustworthy: a cheap model can never approve an ineligible
creator, and changing a criterion in the admin UI provably changes outcomes.

The conditions themselves live in the verified-metric registry (``metrics.py``).
This module only applies them: to each gift offer, and to pitch scoring.
"""

from __future__ import annotations

import re
from typing import Any

from . import metrics
from .metrics import fmt_int, fmt_money, fmt_pct  # noqa: F401  re-exported
from .schemas import BrandConfig, CreatorProfile, GiftOffer


def verified_values(creator: CreatorProfile) -> dict[str, Any]:
    """Every registry metric, read from the verified profile, for the audit log."""
    return {metric.key: metric.getter(creator) for metric in metrics.METRICS}


# ---------------------------------------------------------------------------
# Gifting: one evaluation per gift offer
# ---------------------------------------------------------------------------
def evaluate_offer(creator: CreatorProfile, offer: GiftOffer) -> dict[str, Any]:
    """Apply one gift's *own* criteria to the verified profile."""
    outcome = metrics.evaluate_rules(
        [rule.model_dump() for rule in offer.criteria], creator
    )
    return {
        "gift_id": offer.id,
        "name": offer.name,
        "sku": offer.sku,
        "url": offer.url,
        "retail_price": offer.retail_price,
        "description": offer.description,
        "tags": list(offer.tags),
        "criteria": [rule.describe() for rule in offer.criteria],
        "open_to_all": not offer.criteria,
        "eligible": outcome["eligible"],
        "checks": outcome["checks"],
        "failed": outcome["failed"],
        "failed_labels": outcome["failed_labels"],
        "shortfalls": outcome["shortfalls"],
    }


def _offer_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "gift_id": item["gift_id"],
        "name": item["name"],
        "sku": item["sku"],
        "url": item["url"],
        "retail_price": item["retail_price"],
        "eligible": item["eligible"],
        "shortfalls": item["shortfalls"],
    }


def evaluate_gifting(
    creator: CreatorProfile,
    config: BrandConfig,
    *,
    gift_id: str | None = None,
    sku: str | None = None,
) -> dict[str, Any]:
    """Evaluate the brand's gift offers for this creator.

    With no selector every offer is evaluated. With ``gift_id`` or ``sku`` only
    that offer is -- which is exactly what ``issue_gift`` re-checks before it
    places an order.
    """
    offers = list(config.gifting.offers)
    selected: str = "all"
    if gift_id or sku:
        identifier = gift_id or sku or ""
        match = config.gifting.resolve(identifier)
        offers = [match] if match is not None else []
        # Canonical id, so callers can compare `selected` against gift ids even
        # when the model passed a SKU or a product name.
        selected = match.id if match is not None else identifier.strip().lower()

    evaluations = [evaluate_offer(creator, offer) for offer in offers]
    eligible = [item for item in evaluations if item["eligible"]]
    ineligible = [item for item in evaluations if not item["eligible"]]

    return {
        "eligible": bool(eligible),
        "selected": selected,
        "found": bool(evaluations),
        "offers": evaluations,
        "eligible_offers": [_offer_summary(item) for item in eligible],
        "ineligible_offers": [_offer_summary(item) for item in ineligible],
        "failed_labels": list(
            dict.fromkeys(label for item in ineligible for label in item["failed_labels"])
        ),
        "creator_id": creator.id,
        "verified": verified_values(creator),
    }


def find_offer(config: BrandConfig, sku: str) -> GiftOffer | None:
    """Resolve an offer by id, SKU or name (see ``GiftingConfig.resolve``)."""
    return config.gifting.resolve(sku)


# ---------------------------------------------------------------------------
# Gift discovery -- deterministic keyword matching, no embeddings
# ---------------------------------------------------------------------------
_STOPWORDS = {
    "a", "an", "the", "for", "of", "to", "me", "my", "i", "is", "are", "am", "can", "could",
    "would", "please", "some", "any", "something", "want", "wants", "like", "get", "send",
    "try", "and", "or", "with", "that", "this", "it", "you", "your", "have", "has", "do",
    "does", "just", "about", "was", "were", "be", "been", "there", "their", "on", "in", "at",
    "hi", "hey", "hello", "thanks", "thank", "maybe", "also", "really", "very",
}


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", (text or "").lower())
        if token and token not in _STOPWORDS
    }


def search_offers(
    config: BrandConfig, query: str = "", *, limit: int | None = None
) -> list[dict[str, Any]]:
    """Rank the brand's gift offers against a free-text query.

    Deterministic on purpose: token overlap across name, description, SKU and the
    admin's tags. An empty query returns the whole catalog in configured order.
    """
    wanted = _tokens(query)
    ranked: list[tuple[float, int, GiftOffer, list[str]]] = []
    for index, offer in enumerate(config.gifting.offers):
        haystack = _tokens(
            " ".join([offer.name, offer.description, offer.sku, " ".join(offer.tags)])
        )
        overlap = wanted & haystack
        score = round(len(overlap) / len(wanted), 4) if wanted else 0.0
        if wanted and not overlap:
            continue
        ranked.append((score, index, offer, sorted(overlap)))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    if limit is not None:
        ranked = ranked[:limit]
    return [
        {
            "gift_id": offer.id,
            "name": offer.name,
            "sku": offer.sku,
            "url": offer.url,
            "description": offer.description,
            "retail_price": offer.retail_price,
            "tags": list(offer.tags),
            "match_score": score,
            "matched_on": matched,
        }
        for score, _index, offer, matched in ranked
    ]


# ---------------------------------------------------------------------------
# Pitch fit scoring
# ---------------------------------------------------------------------------
def _ratio(actual: float, ideal: float) -> float:
    if ideal <= 0:
        return 0.0
    return max(0.0, min(1.0, actual / ideal))


def _category_ratio(creator: CreatorProfile, ideal: list[str]) -> tuple[float, str]:
    preferred = {str(item).lower() for item in ideal}
    actual = {str(item).lower() for item in creator.categories}
    overlap = sorted(preferred & actual)
    if overlap:
        return 1.0, f"matches {', '.join(overlap)}"
    return 0.0, f"no overlap with {', '.join(sorted(preferred))}"


def pitch_evaluation(creator: CreatorProfile, config: BrandConfig) -> dict[str, Any]:
    """Score the creator on the metrics the admin selected, then pick a tag.

    The score is always 0-100: component points are divided by the total
    configured weight, so changing a weight changes the balance between metrics
    without silently changing what the thresholds mean.
    """
    components: list[dict[str, Any]] = []
    for item in config.pitch.scoring:
        metric = metrics.get_metric(item.metric)
        actual = metric.getter(creator)
        if metric.shape == "list":
            ratio, detail = _category_ratio(creator, item.ideal)
        else:
            ratio = _ratio(float(actual or 0), float(item.ideal))
            detail = (
                f"{metrics.fmt_value(metric, actual)} vs ideal "
                f"{metrics.fmt_value(metric, item.ideal)}"
            )
        components.append(
            {
                "key": item.metric,
                "metric": item.metric,
                "label": metric.label,
                "ratio": round(ratio, 4),
                "weight": item.weight,
                "points": round(ratio * item.weight, 2),
                "ideal": item.ideal,
                "ideal_display": metrics.fmt_value(metric, item.ideal),
                "actual": actual,
                "actual_display": metrics.fmt_value(metric, actual),
                "detail": detail,
            }
        )

    total_weight = sum(component["weight"] for component in components) or 1.0
    score = round(sum(component["points"] for component in components) / total_weight * 100, 2)
    label = config.pitch.label_for_score(score)

    strengths = [f"{c['label']}: {c['detail']}" for c in components if c["ratio"] >= 0.8]
    concerns = [f"{c['label']}: {c['detail']}" for c in components if c["ratio"] < 0.4]

    return {
        "score": score,
        "label": label.label,
        "decision": label.decision,
        "response_instructions": label.response_instructions,
        "labels": [item.label for item in config.pitch.classification_labels],
        "components": components,
        "strengths": strengths,
        "concerns": concerns,
        "total_weight": total_weight,
        "creator_id": creator.id,
        "verified": verified_values(creator),
    }


def label_for_score(score: float, config: BrandConfig) -> str:
    """Highest configured label whose threshold the score reaches."""
    return config.pitch.label_for_score(score).label

