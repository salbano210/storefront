"""Routing agent: classify intent, then hand off to a specialist."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..schemas import BrandConfig, CreatorProfile
from ..services.llm_client import LLMClient
from . import prompts

AGENT_FOR_INTENT = {
    "gift_request": "gift_request_agent",
    "creator_pitch": "creator_pitch_agent",
    "general_inquiry": "general_handler",
}

_GIFT_PATTERNS = (
    r"\bgift(s|ed|ing)?\b",
    r"\bfree\b",
    r"\bpr package\b",
    r"\bsample(s)?\b",
    r"\bseed(ed|ing)?\b",
    r"\bsend (me|us)\b",
    r"\bsend over\b",
    r"\bcomplimentary\b",
    r"\btry (it|them|the)\b",
    r"\bwould love (to try|the|a|some|one|that)\b",
    r"\bi'?d love (to try|the|a|some|one|that)\b",
    r"\b(can|could|may) i (please )?(get|have|try|receive|grab|order)\b",
    r"\b(can|could) you (please )?(send|ship|mail)\b",
    r"\brequest (the|a|some)\b",
    r"\bunbox(ing)?\b",
)

# Unambiguous gifting language -- wins when a message also sounds like a pitch.
_STRONG_GIFT_PATTERNS = (
    r"\bfree\b",
    r"\bgift(s|ed|ing)?\b",
    r"\bpr package\b",
    r"\bsample(s)?\b",
    r"\bcomplimentary\b",
    r"\bseed(ed|ing)?\b",
    r"\bunbox(ing)?\b",
)

_PITCH_PATTERNS = (
    r"\bpartner(ship|ing)?\b",
    r"\bcollab(orat\w*)?\b",
    r"\bwork(ing)? together\b",
    r"\bpitch\b",
    r"\bmedia kit\b",
    r"\baffiliate\b",
    r"\bcampaign\b",
    r"\bsponsor(ship|ed)?\b",
    r"\bpromote\b",
    r"\brepresent (your|the) brand\b",
    r"\bpaid\b",
)


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def keyword_intent(text: str) -> str | None:
    """Deterministic fallback used when the router returns unusable JSON."""
    gift = _matches(text, _GIFT_PATTERNS)
    pitch = _matches(text, _PITCH_PATTERNS)
    if gift and not pitch:
        return "gift_request"
    if pitch and not gift:
        return "creator_pitch"
    if gift and pitch:
        # "Send me the serum and I'd love to partner" -> gifting; a pure pitch
        # ("I'd love to partner on a campaign") stays a pitch.
        return "gift_request" if _matches(text, _STRONG_GIFT_PATTERNS) else "creator_pitch"
    return None


@dataclass
class RouteResult:
    intent: str
    confidence: float
    reasoning: str
    clarifying_question: str = ""
    used_fallback: bool = False
    raw: dict | None = None

    @property
    def agent(self) -> str:
        return AGENT_FOR_INTENT[self.intent]


async def route(
    llm: LLMClient,
    *,
    config: BrandConfig,
    creator: CreatorProfile,
    history: list[dict],
    user_message: str,
    current_agent: str | None,
    awaiting: bool,
) -> RouteResult:
    system = prompts.router_prompt(
        config, creator, current_agent=current_agent or "", awaiting=awaiting
    )
    result = await llm.complete(
        model=config.models.router,
        system=system,
        messages=[*history, {"role": "user", "content": user_message}],
        agent="router_agent",
        schema=prompts.ROUTER_SCHEMA,
        schema_name="IntentClassification",
        temperature=0.0,
        max_tokens=400,
    )

    parsed = result.parsed or {}
    intent = parsed.get("intent")
    if intent not in AGENT_FOR_INTENT:
        heuristic = keyword_intent(user_message) or "general_inquiry"
        return RouteResult(
            intent=heuristic,
            confidence=0.3,
            reasoning="Router returned unusable JSON; deterministic keyword fallback applied.",
            used_fallback=True,
            raw=parsed,
        )

    try:
        confidence = float(parsed.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5

    return RouteResult(
        intent=intent,
        confidence=max(0.0, min(1.0, confidence)),
        reasoning=str(parsed.get("reasoning", ""))[:600],
        clarifying_question=str(parsed.get("clarifying_question") or "")[:300],
        raw=parsed,
    )
