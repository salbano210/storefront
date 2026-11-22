"""Intent agent: decide whether a message is an intent we have an agent for.

There is exactly one specialist -- the gifting agent. Everything else is routed
to the front desk, which is a responder rather than a specialist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..schemas import BrandConfig, CreatorProfile
from ..services.llm_client import LLMClient
from . import prompts

AGENT_FOR_INTENT = {
    "gift_request": "gift_request_agent",
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

def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def keyword_intent(text: str) -> str:
    """Deterministic fallback used when the router returns unusable JSON.

    With a single specialist agent the only question worth asking is whether the
    message asks for product; everything else belongs to the front desk.
    """
    return "gift_request" if _matches(text, _GIFT_PATTERNS) else "general_inquiry"


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
        messages=[
            *history,
            prompts.creator_turn(user_message),
        ],
        agent="router_agent",
        schema=prompts.ROUTER_SCHEMA,
        schema_name="IntentClassification",
        temperature=0.0,
        max_tokens=400,
    )

    parsed = result.parsed or {}
    intent = parsed.get("intent")
    if intent not in AGENT_FOR_INTENT:
        heuristic = keyword_intent(user_message)
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
