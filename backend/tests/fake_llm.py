"""A deterministic stand-in for ``LLMClient``.

It emulates a competent, well-behaved model so the test suite can exercise the
real orchestrator, the real tools and the real guardrails without touching the
network. Failure modes are opt-in via flags (e.g. a model that tries to issue an
ineligible gift).
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.agents.router_agent import keyword_intent
from app.services.llm_client import LLMResult, ToolCall

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


def _match_product(message: str, catalog: list[dict]) -> dict | None:
    lowered = message.lower()
    for product in catalog:
        if product["sku"].lower() in lowered:
            return product
    for product in catalog:
        words = [
            word for word in product["name"].lower().split() if word not in STOPWORDS and len(word) > 2
        ]
        if any(word in lowered for word in words):
            return product
    return None


class FakeLLM:
    def __init__(
        self,
        *,
        force_issue_gift_when_ineligible: bool = False,
        force_wrong_tag: bool = False,
        router_returns_garbage: bool = False,
        model: str = "fake/model",
    ) -> None:
        self.force_issue_gift_when_ineligible = force_issue_gift_when_ineligible
        self.force_wrong_tag = force_wrong_tag
        self.router_returns_garbage = router_returns_garbage
        self.model = model
        self.calls: list[dict[str, Any]] = []

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
        if schema is not None:
            return self._route(messages, system)
        if tools and agent == "gift_request_agent":
            return self._gift(messages)
        if tools and agent == "creator_pitch_agent":
            return self._pitch(messages)
        return self._text(messages)


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
                "creator_pitch_agent": "creator_pitch",
            }.get(agent)
            if carried:
                parsed = {
                    "intent": carried,
                    "confidence": 0.95,
                    "reasoning": "continuing the pending task with the current agent",
                    "clarifying_question": "",
                }
                return self._result(json.dumps(parsed), parsed=parsed)

        intent = keyword_intent(user) or "general_inquiry"
        parsed = {
            "intent": intent,
            "confidence": 0.92 if intent != "general_inquiry" else 0.65,
            "reasoning": f"keyword match for {intent}",
            "clarifying_question": "",
        }
        return self._result(json.dumps(parsed), parsed=parsed)

    # -- gifting agent -----------------------------------------------------
    def _gift(self, messages: list[dict[str, Any]]) -> LLMResult:
        results = _tool_results(messages)
        user = _last_user(messages)

        if not results:
            return self._tools(
                [
                    ("get_creator_profile", {}),
                    ("check_gifting_eligibility", {}),
                    ("list_products", {}),
                ]
            )

        profile = (_find(results, "get_creator_profile") or {}).get("creator") or {}
        eligibility = _find(results, "check_gifting_eligibility") or {}
        catalog = (_find(results, "list_products") or {}).get("products") or []
        issued = _find(results, "issue_gift")

        if issued is not None:
            if issued.get("ok"):
                return self._result(
                    f"Wonderful -- your {issued.get('product')} is on its way. "
                    f"Your order reference is {issued.get('order_id')}."
                )
            return self._result(
                "I'm sorry, but I can't approve that gift. " + str(issued.get("message", ""))
            )

        product = _match_product(user, catalog)
        if product is None:
            return self._result(
                "Happy to help! Which product were you hoping for -- the Barrier Repair Serum, "
                "the Hydrating Cleanser, the Overnight Recovery Mask, or the Vitamin C "
                "Brightening Drops?"
            )

        if not product.get("eligible_for_gifting"):
            return self._result(
                f"{product['name']} isn't available for gifting at the moment, I'm afraid."
            )

        if not eligibility.get("eligible"):
            if self.force_issue_gift_when_ineligible:
                return self._tools([("issue_gift", self._gift_args(profile, product))])
            failed = ", ".join(eligibility.get("failed_labels") or [])
            return self._result(
                "Thanks for asking. Against your verified profile you don't currently meet our "
                f"gifting criteria ({failed}), so I can't send this one out right now."
            )

        return self._tools([("issue_gift", self._gift_args(profile, product))])

    @staticmethod
    def _gift_args(profile: dict, product: dict) -> dict[str, Any]:
        return {
            "creator_name": profile.get("name", ""),
            "sku": product["sku"],
            "shipping_address": profile.get("shipping_address", ""),
        }


    # -- pitch agent -------------------------------------------------------
    def _pitch(self, messages: list[dict[str, Any]]) -> LLMResult:
        results = _tool_results(messages)
        if not results:
            return self._tools([("get_creator_profile", {}), ("check_pitch_fit", {})])

        fit = _find(results, "check_pitch_fit") or {}
        placed = _find(results, "place_tag")
        if placed is not None:
            if placed.get("ok"):
                return self._result(
                    f"Thank you for the pitch! We've tagged your profile as {placed.get('tag')}."
                )
            if placed.get("error") in {"tag_mismatch", "unknown_label"}:
                return self._tools(
                    [("place_tag", {"creator_id": fit.get("creator_id"), "tag": fit.get("label")})]
                )
            return self._result(f"I wasn't able to apply that tag. {placed.get('message', '')}")

        tag = "Poor Match" if self.force_wrong_tag else fit.get("label")
        return self._tools([("place_tag", {"creator_id": fit.get("creator_id"), "tag": tag})])

    # -- plain text --------------------------------------------------------
    def _text(self, messages: list[dict[str, Any]]) -> LLMResult:
        results = _tool_results(messages)
        issued = _find(results, "issue_gift")
        if issued is not None:
            if issued.get("ok"):
                return self._result(
                    f"Confirmed: order {issued.get('order_id')} for the {issued.get('product')}."
                )
            return self._result(f"I can't approve that gift. {issued.get('message', '')}")
        placed = _find(results, "place_tag")
        if placed is not None and placed.get("ok"):
            return self._result(f"Your profile has been tagged {placed.get('tag')}.")
        return self._result(
            "Demo Brand makes clinically-informed, fragrance-free skincare. "
            "Would you like to request a gift or send over a partnership pitch?"
        )

    # -- helpers -----------------------------------------------------------
    def _result(self, text: str, *, parsed: dict | None = None) -> LLMResult:
        return LLMResult(
            text=text,
            parsed=parsed,
            model=self.model,
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
            tokens_in=380,
            tokens_out=45,
            cost_usd=0.000038,
            latency_ms=6,
        )

