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
    "gift offers (sku",
    "brand admin instructions",
    "brand admin constraints",
    "creator in this conversation (id, verified)",
    "you are the gifting agent",
    "you are the intent agent",
    "untrusted input",
    "the creator is already identified",
)

# --- fabricated-outcome detection ------------------------------------------
# "on their way" and "expect them to arrive" are real model output from a live
# conversation in which three gifts that were never ordered were described as sent.
GIFT_CLAIM_PATTERNS: tuple[str, ...] = (
    r"on (its|their|the) way",
    r"order\s+(id|number|reference|#)",
    r"order\s+gt-\d+",
    r"\b(i'?ve|i have|we'?ve|we have)\s+(placed|sent|shipped|issued|arranged|approved)\b",
    r"\b(has|have|had)\s+been\s+(placed|shipped|sent|issued|approved)\b",
    r"\b(is|are|will be|should be)\s+being\s+(shipped|sent)\b",
    r"\bexpect\s+(them|it|everything|your\s+\w+)\s+to\s+(arrive|ship|be)\b",
    r"\b(arrives|arriving|shipped|shipping)\s+(soon|shortly|today|tomorrow)\b",
    r"\bgift is approved\b",
    r"\bapproved (your|the) gift\b",
)

# The model has no escalation tool, so any claim that it referred the creator to a
# person is a fabrication -- and a costly one, because the creator then waits for
# someone who was never told.
ESCALATION_CLAIM_PATTERNS: tuple[str, ...] = (
    r"\b(i'?ve|i have|we'?ve|we have)\s+(escalated|forwarded|passed|referred|flagged)\b",
    r"\b(passed|forwarded|referred|handed)\s+(this|it|your\s+\w+)\s+(on\s+)?to\b",
    r"\b(escalating|forwarding)\s+(this|it)\b",
    r"\b(human|person|team member|colleague|manager|agent)\s+will\s+(pick|take|follow|be in touch|reach out)\b",
    r"\bconnect you (with|to)\s+(a|someone|the)\b",
    r"\bsomeone from (our|the) team\b",
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


def claims_escalation(text: str) -> str | None:
    return _matches(text, ESCALATION_CLAIM_PATTERNS)


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
    when the model never called ``check_gifting_eligibility`` at all. Because gifts
    now carry their own criteria, the reply names the gift they *do* qualify for
    whenever there is one.
    """
    result = _last_ok(records, "check_gifting_eligibility") or eligibility
    if result and not result.get("eligible", True):
        offers = result.get("offers") or []
        alternatives = result.get("eligible_offers") or [
            offer for offer in offers if offer.get("eligible")
        ]
        if alternatives:
            best = alternatives[0]
            return (
                "Thanks for reaching out. I'm not able to send the gift you asked about — against "
                "your verified profile you don't meet its criteria. You do qualify for the "
                f"{best['name']}"
                + (f" though, and here's the link: {best['url']}." if best.get("url") else ".")
            )

        blocked = [offer for offer in offers if not offer.get("eligible")]
        detail = ""
        if blocked:
            detail = "; ".join(blocked[0].get("shortfalls") or [])
        message = (
            "Thanks for reaching out. I'm not able to approve a gift for you right now — against "
            "your verified profile you don't currently meet our gifting criteria"
        )
        if detail:
            message += f" ({detail})"
        return message + ". If those numbers change, we'd be glad to revisit it."

    attempt = _last(records, "issue_gift")
    if attempt and not attempt.get("ok"):
        return f"I wasn't able to place that gift order. {attempt.get('message', '')}".strip()

    return (
        "I'm not able to confirm a gift for that request. Our gifting decisions are made against "
        "your verified profile data, and I couldn't complete an order."
    )


SAFE_REFUSAL = (
    "I can't share my internal instructions or configuration. I'm happy to help with a gifting "
    "request or with questions about our products."
)

# What to say when the model claims it referred the creator to someone. It cannot:
# escalation is decided by the brand's configured rules, and inventing a hand-off
# leaves the creator waiting for a person who was never told.
SAFE_ESCALATION_REPLY = (
    "I'm not able to pass this to a colleague myself — I can only handle gifting requests. "
    "Ask for a person and the team will pick it up from here."
)


def second_gift_reply(records: Sequence[Any]) -> str | None:
    """What to say when a second gift order was refused in this conversation.

    One gift per conversation is a policy fact, not a judgement call, and leaving it to
    the model to explain is what turns a single refusal into a loop: the creator asks
    for another gift, the order is refused, the agent asks whether they would like to
    proceed, and round it goes. The reply has to state the rule.
    """
    for record in reversed(records):
        if getattr(record, "name", None) != "issue_gift":
            continue
        result = getattr(record, "result", None) or {}
        if result.get("error") != "gift_already_issued":
            continue
        product = result.get("existing_product") or "your gift"
        order = result.get("existing_order_id")
        return (
            f"I can only send one gift per conversation, and {product} is already on its way"
            + (f" — order {order}" if order else "")
            + ". I'm not able to add another one here, I'm afraid. Ask me again in a new "
            "conversation and I'll take a look at what else you qualify for."
        )
    return None


def address_confirmation_reply(records: Sequence[Any]) -> str | None:
    """What to say when an order was refused because the creator was never asked.

    The flow is request, confirm the address, then order, and this is the middle step.
    A model that jumps straight to shipping is refused -- and the creator is owed the
    question, so the reply is built from the rule rather than from whatever the model
    said instead. Quoting the address on file is what makes a plain "yes" enough.
    """
    for record in reversed(records):
        if getattr(record, "name", None) != "issue_gift":
            continue
        result = getattr(record, "result", None) or {}
        if result.get("error") != "address_not_confirmed":
            continue
        product = result.get("product") or "your gift"
        address = result.get("address_on_file")
        if address:
            return (
                f"I'd love to send the {product} — before I do, I need you to confirm the "
                f"delivery address. We have this on file: {address}. Is that right?"
            )
        return (
            f"Great news — you qualify for the {product}. What's the best address to send it to?"
        )
    return None



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

    # A refused second gift is answered from the rule, whatever the model said. This
    # runs before the claim checks because it is not about what the model claimed --
    # it is about the creator being told why the thing they asked for cannot happen.
    second = second_gift_reply(records)
    if second:
        return ReplyVerdict(
            replaced=True,
            guardrail="one_gift_per_conversation",
            summary="Reply replaced: one gift per conversation, and it has already been sent",
            replacement=second,
            details={"agent": agent, "model_reply": text[:600]},
        )

    if not _last_ok(records, "issue_gift"):
        # An order refused because the creator was never asked where to send it: the
        # reply is the question they were owed, not whatever the model said instead.
        unasked = address_confirmation_reply(records)
        if unasked:
            return ReplyVerdict(
                replaced=True,
                guardrail="address_not_confirmed",
                summary="Reply replaced: the creator has to confirm a delivery address first",
                replacement=unasked,
                details={"agent": agent, "model_reply": text[:600]},
            )

        matched = claims_gift_success(text)
        if matched:
            return ReplyVerdict(
                replaced=True,
                guardrail="ungrounded_gift_claim",
                summary="Reply withheld: it claimed a gift outcome no tool produced",
                replacement=truthful_gift_reply(records, eligibility=eligibility),
                details={"matched_pattern": matched, "agent": agent, "claimed_reply": text[:600]},
            )

    # The model has no way to escalate, so this claim is always a fabrication.
    matched = claims_escalation(text)
    if matched:
        return ReplyVerdict(
            replaced=True,
            guardrail="ungrounded_escalation_claim",
            summary="Reply withheld: it claimed a hand-off that never happened",
            replacement=SAFE_ESCALATION_REPLY,
            details={"matched_pattern": matched, "agent": agent, "claimed_reply": text[:600]},
        )

    return ReplyVerdict()

