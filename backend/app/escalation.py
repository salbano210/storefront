"""Escalation: taking a conversation off the agent and leaving it to a person.

Two kinds of trigger, both configured by the admin and both evaluated in Python:

* **keyword** rules match the creator's own words, so "I need to speak to a human"
  or a safety complaint reaches a person without the model being involved;
* **metric** rules match the verified creator profile, so an audience-quality flag
  or a VIP account is handled by a human rather than decided by an agent.

The check runs *before* the router and before any model call. That is what makes it
a real hand-off rather than something the agent says: an escalated turn costs
nothing, calls no tools, and nothing in the creator's message can talk it out of
escalating. The model has no escalation tool, and a reply that claims otherwise is
caught by the guardrails.

Because it runs first, it also **overrides gift eligibility**. A creator who clears
every rule on the gift they asked for is still handed to a human if they trip a
trigger here -- escalation is the more specific instruction, and the two are not
arbitrated, because there is nothing to arbitrate: the conversation never reaches
the gift agent.

Reasons are internal. They name the trigger for whoever picks the conversation up
and are never quoted back to the creator -- a keyword like "audience quality needs
a look" is not something to say to the person it is about.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import metrics
from .schemas import BrandConfig, CreatorProfile


@dataclass(frozen=True)
class Escalation:
    """Why a conversation is being handed to a human."""

    kind: str  # "keyword" | "metric"
    reason: str
    detail: str
    check: dict[str, Any] | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind,
            "reason": self.reason,
            "detail": self.detail,
        }
        if self.check is not None:
            out["check"] = self.check
        return out

    def summary(self) -> str:
        return f"Escalated to a human ({self.kind}): {self.detail}"


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    """Whole-word match, with any suffix allowed on the final word.

    So "refund" catches "refunds" and "refunded", while "sue" does not catch
    "issue" -- the leading boundary is what stops the false positives that a plain
    substring search produces.
    """
    escaped = re.escape(" ".join(phrase.split()))
    return re.compile(rf"(?<!\w){escaped}\w*", re.IGNORECASE)


def keyword_escalation(message: str, config: BrandConfig) -> Escalation | None:
    """The first configured keyword the creator's message matches."""
    text = message or ""
    for keyword in config.escalation.keywords:
        if _phrase_pattern(keyword.phrase).search(text):
            return Escalation(
                kind="keyword",
                reason=keyword.reason or "Creator asked for a human",
                detail=keyword.phrase,
            )
    return None


def metric_escalation(
    profile: CreatorProfile, config: BrandConfig
) -> Escalation | None:
    """The first configured metric rule the verified profile satisfies.

    Verified values only, like every other rule in this system: a creator cannot
    talk their way onto, or off, a human's desk.
    """
    for rule in config.escalation.metrics:
        check = metrics.evaluate_rule(rule.model_dump(), profile)
        if check["passed"]:
            return Escalation(
                kind="metric",
                reason=rule.reason or "Flagged by a profile rule",
                detail=rule.describe(),
                check=check,
            )
    return None


def evaluate(
    profile: CreatorProfile, config: BrandConfig, message: str
) -> Escalation | None:
    """Should this turn go to a human? Keywords first, then the profile.

    Keywords win because they are about *this message*: if the creator just asked
    for a person, that is the reason worth recording, whatever their profile says.
    """
    return keyword_escalation(message, config) or metric_escalation(profile, config)
