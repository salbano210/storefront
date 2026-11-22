"""Deterministic business rules.

This module is deliberately boring and contains no LLM calls. The agents gather
information and talk to the creator; *these* functions decide. That separation is
what makes the demo trustworthy: a cheap model can never approve an ineligible
creator, and changing a criterion in the admin UI provably changes outcomes.

The conditions themselves live in the verified-metric registry (``metrics.py``).
This module only applies them: to each gift offer.
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
