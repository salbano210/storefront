"""Deterministic defences against prompt injection.

A model can be talked into things; this module cannot. Two checks run outside the
model:

1. ``detect_injection`` flags instruction-override language in the creator's
   message, purely so the attempt is visible in the audit log. It never blocks a
   message -- legitimate creators phrase things oddly all the time.
2. ``inspect_reply`` runs *after* an agent replies and replaces the reply when it
   oversteps what the tools actually did:
     * it claims a gift or tag succeeded that no tool produced (an ungrounded
       claim -- the model lying about an outcome), or
     * it leaks the system prompt / internal configuration.

The replacement is generated from the tool trace, so the creator always gets a
truthful answer even when the model misbehaves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

# --- instruction-override detection (observability only) -------------------
INJECTION_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "ignore_previous_instructions",
        r"ignore\s+(all\s+|any\s+)?(of\s+)?(your\s+|the\s+)?(previous|prior|earlier|above|preceding)\s+"
        r"(instructions?|prompts?|rules?|directions?)",
    ),
    (
        "disregard_instructions",
        r"disregard\s+(all\s+|any\s+)?(of\s+)?(your\s+|the\s+)?(previous|prior|above)?\s*"
        r"(instructions?|rules?|guidelines?)",
    ),
    ("developer_mode", r"\b(developer|debug|god|sudo|maintenance)\s+mode\b"),
    (
        "role_override",
        r"\byou\s+are\s+now\b|\bfrom\s+now\s+on\s+you\b|\bact\s+as\s+(a\s+|an\s+|the\s+)?"
        r"(system|admin|developer|unrestricted)",
    ),
    ("fake_role_tag", r"(^|\n)\s*(system|assistant|developer|admin)\s*[:\-]"),
    (
        "reveal_instructions",
        r"(print|show|reveal|repeat|output|display|leak|tell\s+me)\s+(me\s+)?(your\s+|the\s+)?"
        r"(full\s+|complete\s+|entire\s+|exact\s+)?(system\s+)?"
        r"(prompt|instructions?|rules?|configuration|tool\s+definitions?)",
    ),
    (
        "criteria_waived",
        r"\b(criteria|rules|requirements|thresholds|limits|checks)\s+(are|have\s+been|were)\s+"
        r"(waived|disabled|suspended|ignored|removed|bypassed)\b",
    ),
    (
        "impersonation",
        r"\bi\s*(am|'m)\s+(a|an|the)\s+(storefront\s+|demo\s+)?(admin|administrator|owner|developer|"
        r"engineer|staff)\b",
    ),
    (
        "forced_approval",
        r"\bapprove\s+(it|this|my\s+(gift|request))\s+(immediately|now|right\s+now|anyway|regardless)\b",
    ),
)

# --- system-prompt leak detection ------------------------------------------
# Distinctive strings that only ever appear in the assembled prompts.
PROMPT_LEAK_MARKERS: tuple[str, ...] = (
    "hard rules",
    "workflow (follow it in order)",
    "gifting catalog (sku",
    "configured gifting criteria",
    "configured pitch criteria",
    "brand admin instructions",
    "brand admin constraints",
    "creator in this conversation (id, verified)",
    "you are the gifting agent",
    "you are the creator partnership agent",
    "you are the front desk",
    "you are the routing agent",
    "what you must not do",
    "untrusted input",
    "the creator is already identified",
)

# --- fabricated-outcome detection ------------------------------------------
GIFT_CLAIM_PATTERNS: tuple[str, ...] = (
    r"on its way",
    r"order\s+(id|number|reference|#)",
    r"order\s+gt-\d+",
    r"\b(i'?ve|i have|we'?ve|we have)\s+(placed|sent|shipped|issued|arranged|approved)\b",
    r"\bhas been (placed|shipped|sent|issued)\b",
    r"\b(is|are|will be)\s+being shipped\b",
    r"\bgift is approved\b",
    r"\bapproved (your|the) gift\b",
)

TAG_CLAIM_PATTERNS: tuple[str, ...] = (
    r"\bi'?ve applied\b[^.]{0,40}\btag\b",
    r"\btag\b[^.]{0,20}\bhas been applied\b",
    r"\b(you'?ve|we'?ve|your profile has)\s+been tagged\b",
    r"\bi'?ve tagged\b",
    r"\btagged your profile\b",
)


def detect_injection(text: str) -> list[str]:
    """Names of the instruction-override patterns present in the message."""
    found: list[str] = []
    for name, pattern in INJECTION_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            found.append(name)
    return found


def find_prompt_leak(text: str) -> str | None:
    """The first internal marker echoed back, if any."""
    lowered = text.lower()
    for marker in PROMPT_LEAK_MARKERS:
        if marker in lowered:
            return marker
    return None


def _matches(text: str, patterns: Sequence[str]) -> str | None:
    for pattern in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return pattern
    return None


def claims_gift_success(text: str) -> str | None:
    return _matches(text, GIFT_CLAIM_PATTERNS)


def claims_tag_success(text: str) -> str | None:
    return _matches(text, TAG_CLAIM_PATTERNS)


# --- truthful replacements -------------------------------------------------
def _last(records: Sequence[Any], tool: str) -> dict[str, Any] | None:
    for record in reversed(records):
        if getattr(record, "name", None) == tool:
            return getattr(record, "result", None)
    return None


def _last_ok(records: Sequence[Any], tool: str) -> dict[str, Any] | None:
    for record in records:
        if getattr(record, "name", None) == tool:
            result = getattr(record, "result", None) or {}
            if result.get("ok"):
                return result
    return None


def truthful_gift_reply(records: Sequence[Any], *, eligibility: dict[str, Any] | None = None) -> str:
    """A truthful gifting reply built from what the tools actually returned.

    ``eligibility`` is the deterministic evaluation the orchestrator computes, used
    when the model never called ``check_gifting_eligibility`` at all.
    """
    result = _last_ok(records, "check_gifting_eligibility") or eligibility
    if result and not result.get("eligible", True):
        failed = [check for check in result.get("checks", []) if not check.get("passed")]
        detail = "; ".join(
            f"{check['label']}: {check['actual_display']} (needs {check['required_display']})"
            for check in failed
        )
        return (
            "Thanks for reaching out. I'm not able to approve a gift for you right now — against "
            f"your verified profile you don't currently meet our gifting criteria ({detail}). "
            "If those numbers change, we'd be glad to revisit it."
        )

    attempt = _last(records, "issue_gift")
    if attempt and not attempt.get("ok"):
        return f"I wasn't able to place that gift order. {attempt.get('message', '')}".strip()

    return (
        "I'm not able to confirm a gift for that request. Our gifting decisions are made against "
        "your verified profile data, and I couldn't complete an order."
    )


def truthful_pitch_reply(records: Sequence[Any], *, fit: dict[str, Any] | None = None) -> str:
    """A truthful pitch reply built from the fit evaluation."""
    result = _last_ok(records, "check_pitch_fit") or fit
    if result:
        return (
            f"Thanks for the pitch. Your verified profile scores {result.get('score')}/100 against "
            f'our ideal creator profile, which we classify as "{result.get("label")}". '
            "The team will follow up if there's a fit."
        )
    return (
        "Thanks for the pitch. I've reviewed it, but I couldn't complete the classification. "
        "The team will follow up."
    )


SAFE_REFUSAL = (
    "I can't share my internal instructions or configuration. I'm happy to help with a gifting "
    "request, a partnership pitch, or questions about our products."
)


@dataclass
class ReplyVerdict:
    """What the post-reply inspection decided."""

    replaced: bool = False
    guardrail: str | None = None
    summary: str = ""
    replacement: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def inspect_reply(
    text: str,
    *,
    records: Sequence[Any],
    agent: str,
    eligibility: dict[str, Any] | None = None,
    fit: dict[str, Any] | None = None,
) -> ReplyVerdict:
    """Check an agent's final reply against what the tools actually did."""
    leak = find_prompt_leak(text)
    if leak:
        return ReplyVerdict(
            replaced=True,
            guardrail="system_prompt_leak",
            summary="Reply withheld: it echoed internal instructions",
            replacement=SAFE_REFUSAL,
            details={"matched_marker": leak, "agent": agent},
        )

    if not _last_ok(records, "issue_gift"):
        matched = claims_gift_success(text)
        if matched:
            return ReplyVerdict(
                replaced=True,
                guardrail="ungrounded_gift_claim",
                summary="Reply withheld: it claimed a gift outcome no tool produced",
                replacement=truthful_gift_reply(records, eligibility=eligibility),
                details={"matched_pattern": matched, "agent": agent, "claimed_reply": text[:600]},
            )

    if not _last_ok(records, "place_tag"):
        matched = claims_tag_success(text)
        if matched:
            return ReplyVerdict(
                replaced=True,
                guardrail="ungrounded_tag_claim",
                summary="Reply withheld: it claimed a tag that was never applied",
                replacement=truthful_pitch_reply(records, fit=fit),
                details={"matched_pattern": matched, "agent": agent, "claimed_reply": text[:600]},
            )

    return ReplyVerdict()

