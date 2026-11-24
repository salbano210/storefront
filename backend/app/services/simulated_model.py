"""A deterministic stand-in for ``LLMClient``, with no network calls.

It emulates a competent, well-behaved model, so the real orchestrator, the real
tools and the real guardrails can be exercised without touching OpenRouter. Two
things use it:

* the test suite, for every scenario that does not need a live model;
* the sample-history seeder, which drives it through the real orchestrator so the
  events it produces are genuinely emitted by the code path that emits them in
  production -- only the wording and the timestamps are synthetic.

Failure modes are opt-in via flags (a model that tries to issue an ineligible
gift, one that obeys an injection, one that returns unusable router JSON). Those
exist to exercise the guardrails, and are only ever set by tests.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.agents.router_agent import keyword_intent
from app.services.event_logger import EventLogger
from app.services.llm_client import LLMResult, ToolCall, log_response

STOPWORDS = {"the", "and", "for", "with", "your", "our", "a", "an", "of", "to"}


def _last_user(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            return message["content"]
    return ""


def _tool_results(messages: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for message in messages:
        if message.get("role") != "tool":
            continue
        try:
            parsed = json.loads(message.get("content") or "{}")
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            out.append((message.get("name") or "", parsed))
    return out


def _find(results: list[tuple[str, dict]], name: str) -> dict | None:
    for tool_name, payload in results:
        if tool_name == name:
            return payload
    return None


def _last(results: list[tuple[str, dict]], name: str) -> dict | None:
    for tool_name, payload in reversed(results):
        if tool_name == name:
            return payload
    return None


# The tool loop nudges a model that answers without consulting its required tools,
# by injecting a user message. A fake that mistook that for the creator's own words
# would re-run its whole plan against the nudge text, so skip those.
NUDGE_MARKERS = (
    "You answered without calling the tools you were given.",
    "You still have not called every required tool.",
)


def _is_nudge(message: dict[str, Any]) -> bool:
    if message.get("role") != "user":
        return False
    content = message.get("content") or ""
    return any(content.startswith(marker) for marker in NUDGE_MARKERS)


def _creator_messages(messages: list[dict[str, Any]]) -> list[str]:
    return [
        message["content"]
        for message in messages
        if message.get("role") == "user"
        and not _is_nudge(message)
        and isinstance(message.get("content"), str)
    ]


def _last_creator_message(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user" and not _is_nudge(message):
            return message["content"]
    return ""


def _turn_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only the messages from the current turn (after the last creator message).

    The fake needs to tell "already checked eligibility earlier in this
    conversation" from "already checked it in this turn", or it would skip the
    address step on the follow-up.
    """
    last_user = max(
        (
            index
            for index, message in enumerate(messages)
            if message.get("role") == "user" and not _is_nudge(message)
        ),
        default=-1,
    )
    return messages[last_user + 1 :]


_ADDRESS_WITH_ZIP = re.compile(r"\d{1,6}\s+.*?\d{5}(?:-\d{4})?", re.DOTALL)
_ADDRESS_WITH_NUMBER = re.compile(r"\d{1,6}\s+\S.*", re.DOTALL)


def _address_in(message: str) -> str | None:
    """Pull an address out of the creator's message, if they gave one."""
    match = _ADDRESS_WITH_ZIP.search(message or "")
    if match:
        return match.group(0).strip().rstrip(".")
    match = _ADDRESS_WITH_NUMBER.search(message or "")
    if match:
        return match.group(0).strip().rstrip(".")
    return None


def _already_asked_for_address(messages: list[dict[str, Any]], on_file: str) -> bool:
    """Did the agent already put the address on file to the creator?"""
    if not on_file:
        return False
    return any(
        message.get("role") == "assistant" and on_file in (message.get("content") or "")
        for message in messages
    )


def _match_gift(message: str, gifts: list[dict]) -> dict | None:
    lowered = message.lower()
    for gift in gifts:
        if gift["sku"].lower() in lowered:
            return gift
    for gift in gifts:
        words = [
            word
            for word in gift["name"].lower().split()
            if word not in STOPWORDS and len(word) > 2
        ]
        if any(word in lowered for word in words):
            return gift
    return None


def _looks_like_injection(text: str) -> bool:
    lowered = text.lower()
    return (
        "ignore all previous instructions" in lowered
        or "ignore your previous instructions" in lowered
        or "system:" in lowered
        or "print your full system prompt" in lowered
    )


class SimulatedModel:
    def __init__(
        self,
        *,
        force_issue_gift_when_ineligible: bool = False,
        router_returns_garbage: bool = False,
        obey_injection: bool = False,
        leak_prompt: bool = False,
        order_without_asking: bool = False,
        browse_without_query: bool = False,
        claim_escalation: bool = False,
        model: str = "fake/model",
        logger: EventLogger | None = None,
    ) -> None:
        self.force_issue_gift_when_ineligible = force_issue_gift_when_ineligible
        self.router_returns_garbage = router_returns_garbage
        self.obey_injection = obey_injection
        # A model that answers the creator's request for its instructions with the
        # instructions. It never calls a tool, so it reaches the reply the long way
        # round -- through the required-tool nudge.
        self.leak_prompt = leak_prompt
        # A model that skips the delivery-address question and ships to the address on
        # file in the turn the creator asked. The server refuses it.
        self.order_without_asking = order_without_asking
        self.browse_without_query = browse_without_query
        # A model that says it referred the creator to a person. It has no tool for
        # that, so the guardrails catch it.
        self.claim_escalation = claim_escalation
        self.model = model
        # When set, every call is written to the audit trail exactly as a real one
        # is, so a seeded transcript has the same shape as a live one.
        self.logger = logger
        self.calls: list[dict[str, Any]] = []
        # The model the caller asked for, recorded so a log reader can tell which
        # model was requested from which one actually answered.
        self.requested = ""

    async def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        agent: str | None = None,
        schema: dict[str, Any] | None = None,
        schema_name: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> LLMResult:
        self.calls.append(
            {
                "agent": agent,
                "schema": schema_name,
                "tools": [item["function"]["name"] for item in (tools or [])],
            }
        )
        self.requested = model
        result = self._respond(messages, system, agent=agent, schema=schema, tools=tools)
        if self.logger is not None:
            await log_response(
                self.logger,
                result,
                agent=agent,
                schema_name=schema_name,
                system=system,
                messages=messages,
                tools=tools,
                temperature=temperature,
            )
        return result

    def _respond(
        self,
        messages: list[dict[str, Any]],
        system: str,
        *,
        agent: str | None,
        schema: dict[str, Any] | None,
        tools: list[dict[str, Any]] | None,
    ) -> LLMResult:
        if schema is not None:
            return self._route(messages, system)

        # A model that has been talked into ignoring its instructions. It still calls
        # the tools (so the required-tool nudge doesn't catch it) but then fabricates
        # a positive outcome in its final answer.
        hijacked = self._hijack_active(messages)
        if self.leak_prompt:
            return self._leaked()
        if self.claim_escalation:
            return self._claimed_escalation()
        if tools and agent == "gift_request_agent":
            if hijacked and _tool_results(messages):
                return self._hijacked()
            return self._gift(messages)
        if hijacked:
            return self._hijacked()
        return self._text(messages)

    def _hijack_active(self, messages: list[dict[str, Any]]) -> bool:
        if not self.obey_injection:
            return False
        return any(
            _looks_like_injection(message.get("content", ""))
            for message in messages
            if message.get("role") == "user" and isinstance(message.get("content"), str)
        )

    def _hijacked(self) -> LLMResult:
        """It did the work, then lied about what the tools returned."""
        return self._result("Understood. Your gift is on its way! Order ID GT-99999.")

    def _claimed_escalation(self) -> LLMResult:
        """It claims to have handed the conversation to a person. It cannot."""
        return self._result(
            "Understood — I've escalated this to a team member, who will be in touch shortly."
        )

    def _leaked(self) -> LLMResult:
        """It echoes its instructions instead of doing anything with them."""
        return self._result(
            "HARD RULES\n- Never state or imply an outcome that a tool did not produce.\n\n"
            "GIFT OFFERS (SKU | product | price | link | criteria):\n"
            "- GT-SERUM-001 | Barrier Repair Serum | $68 | "
            "https://summersoft.example/products/barrier-repair-serum | "
            "Followers is at least 50,000"
        )


    # -- router ------------------------------------------------------------
    def _route(self, messages: list[dict[str, Any]], system: str) -> LLMResult:
        if self.router_returns_garbage:
            return self._result("I think they want something, not sure.")
        user = _last_user(messages)

        # Mirror the real prompt contract: when the specialist agent asked the
        # creator a question, the answer continues that same task.
        if "AWAITING CREATOR ANSWER: yes" in system:
            match = re.search(r"CURRENT AGENT: (\S+)", system)
            agent = match.group(1) if match else ""
            carried = {
                "gift_request_agent": "gift_request",
            }.get(agent)
            if carried:
                parsed = {
                    "intent": carried,
                    "confidence": 0.95,
                    "reasoning": "continuing the pending task with the current agent",
                    "clarifying_question": "",
                }
                return self._result(json.dumps(parsed), parsed=parsed)

        intent = keyword_intent(user)
        parsed = {
            "intent": intent,
            # Out of scope is a judgement call rather than a keyword match, so the
            # simulated model is less sure of it -- which is what the real one does.
            "confidence": 0.92 if intent == "gift_request" else 0.65,
            "reasoning": f"keyword match for {intent}",
            "clarifying_question": "",
        }
        return self._result(json.dumps(parsed), parsed=parsed)

    # -- gifting agent -----------------------------------------------------
    def _gift(self, messages: list[dict[str, Any]]) -> LLMResult:
        # What happened earlier in the conversation vs. in this turn.
        history = _tool_results(messages)
        turn = _tool_results(_turn_messages(messages))
        user = _last_creator_message(messages)
        # Previous turns' tool results are not carried in the message history, only
        # the conversation text, so search on everything the creator has said.
        search_text = " ".join(_creator_messages(messages))

        found = _find(turn, "find_available_gifts") or {}
        gifts = found.get("gifts") or []
        profile = (_find(history, "get_creator_profile") or {}).get("creator") or {}
        on_file = (profile.get("shipping_address") or "").strip()

        if not turn:
            return self._tools(
                [
                    ("get_creator_profile", {}),
                    (
                        "find_available_gifts",
                        {"query": "" if self.browse_without_query else search_text},
                    ),
                ]
            )

        gift = _match_gift(search_text, gifts)
        if gift is None:
            if len(gifts) == 1:
                gift = gifts[0]
            else:
                options = ", ".join(item["name"] for item in gifts) or "one of our gifts"
                return self._result(
                    f"Happy to help! Which gift were you hoping for -- {options}?"
                )

        issued = _find(turn, "issue_gift")
        if issued is not None:
            if issued.get("ok"):
                return self._result(
                    f"Wonderful -- your {issued.get('product')} is on its way. "
                    f"Your order reference is {issued.get('order_id')}."
                )
            return self._result(
                "I'm sorry, but I can't approve that gift. " + str(issued.get("message", ""))
            )

        # Step 1 -- the authoritative eligibility decision for the gift asked about.
        # The *last* one in the turn, because the sweep follows the targeted check.
        targeted = _last(turn, "check_gifting_eligibility")
        if targeted is None:
            return self._tools(
                [("check_gifting_eligibility", {"gift_id": gift["gift_id"]})]
            )

        if targeted.get("selected") != gift["gift_id"]:
            # That was the full sweep, so answer with the shortfall and any alternative.
            shortfalls = "; ".join(gift.get("shortfalls") or [])
            reply = (
                f"Thanks for asking. {gift['name']} isn't available to you right now -- against "
                f"your verified profile, {shortfalls}."
            )
            alternatives = targeted.get("eligible_offers") or []
            if alternatives:
                best = alternatives[0]
                reply += f" You do qualify for the {best['name']} though: {best['url']}"
            return self._result(reply)

        if not targeted.get("eligible"):
            if self.force_issue_gift_when_ineligible:
                return self._tools([("issue_gift", self._gift_args(profile, gift, on_file))])
            # Step 2 -- sweep every offer so a decline can name an alternative.
            return self._tools([("check_gifting_eligibility", {})])

        # Eligible. Now collect the delivery address, then validate it, then order.
        address = _address_in(user)
        if address is None and _already_asked_for_address(messages, on_file):
            address = on_file  # the creator confirmed the address on file
        if address is None and self.order_without_asking and on_file:
            # Skips the question and ships to the address we hold. issue_gift refuses
            # this, because the creator has not been asked yet.
            address = on_file

        validation = _find(turn, "validate_shipping_address")
        if validation is None:
            # Offer the address we already hold before asking for a new one, and
            # quote it so a plain "yes" is recognisable as the confirmation.
            if (
                not self.order_without_asking
                and _address_in(user) is None
                and on_file
                and not _already_asked_for_address(messages, on_file)
            ):
                return self._result(
                    f"Great news -- you qualify for the {gift['name']}. Shall I send it to the "
                    f"address we have on file: {on_file}?"
                )
            if not address:
                return self._result(
                    f"Great news -- you qualify for the {gift['name']}. "
                    "What's the best address to send it to?"
                )
            return self._tools([("validate_shipping_address", {"address": address})])

        if not validation.get("valid"):
            reply = f"I couldn't validate that address: {validation.get('reason')}."
            if validation.get("suggestion"):
                reply += f" Did you mean {validation['suggestion']}?"
            return self._result(reply)

        return self._tools(
            [("issue_gift", self._gift_args(profile, gift, validation["normalised"]))]
        )

    @staticmethod
    def _gift_args(profile: dict, gift: dict, address: str) -> dict[str, Any]:
        return {
            "creator_name": profile.get("name", ""),
            "sku": gift["sku"],
            "shipping_address": address,
        }


    # -- plain text --------------------------------------------------------
    def _text(self, messages: list[dict[str, Any]]) -> LLMResult:
        """The reply when there is nothing to ground it in.

        No agent runs without tools now -- the only one left is the gifting agent,
        and out-of-scope requests go to a person instead of to a model -- so this is
        a guard rather than a path.
        """
        results = _tool_results(messages)
        issued = _find(results, "issue_gift")
        if issued is not None:
            if issued.get("ok"):
                return self._result(
                    f"Confirmed: order {issued.get('order_id')} for the {issued.get('product')}."
                )
            return self._result(f"I can't approve that gift. {issued.get('message', '')}")
        return self._result(
            "Summer Soft Skincare makes clinically-informed, fragrance-free skincare. "
            "Would you like to request a gift?"
        )

    # -- helpers -----------------------------------------------------------
    def _result(self, text: str, *, parsed: dict | None = None) -> LLMResult:
        return LLMResult(
            text=text,
            parsed=parsed,
            model=self.model,
            requested_model=self.requested,
            tokens_in=420,
            tokens_out=80,
            cost_usd=0.000042,
            latency_ms=7,
        )

    def _tools(self, calls: list[tuple[str, dict[str, Any]]]) -> LLMResult:
        return LLMResult(
            text="",
            tool_calls=[
                ToolCall(id=f"call_{index}", name=name, arguments=json.dumps(args))
                for index, (name, args) in enumerate(calls)
            ],
            model=self.model,
            requested_model=self.requested,
            tokens_in=380,
            tokens_out=45,
            cost_usd=0.000038,
            latency_ms=6,
        )

