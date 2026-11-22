"""Bounded tool-calling loop shared by the specialist agents."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..schemas import BrandConfig, CreatorProfile
from ..services.llm_client import LLMClient
from ..tools.registry import ToolContext, execute_tool, tool_definitions
from . import prompts

MAX_ITERATIONS = 5
FALLBACK_REPLY = (
    "Thanks for reaching out -- I've logged your message and someone from the team will follow up."
)

# A specialist may not answer a business question until it has consulted the
# authoritative tools. Cheap models occasionally skip this step, so the loop
# nudges them (with tool_choice="required") until the tools have been called.
REQUIRED_TOOLS: dict[str, tuple[str, ...]] = {
    "gift_request_agent": ("get_creator_profile", "check_gifting_eligibility"),
}


def _missing_tools(agent_name: str, records: list["ToolCallRecord"]) -> list[str]:
    called = {record.name for record in records}
    return [name for name in REQUIRED_TOOLS.get(agent_name, ()) if name not in called]


@dataclass
class ToolCallRecord:
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]


@dataclass
class AgentRun:
    text: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    model: str = ""

    @property
    def successful(self) -> bool:
        return bool(self.text.strip())

    def result_of(self, tool_name: str) -> dict[str, Any] | None:
        for record in reversed(self.tool_calls):
            if record.name == tool_name:
                return record.result
        return None

    def any_ok(self, tool_name: str) -> dict[str, Any] | None:
        for record in self.tool_calls:
            if record.name == tool_name and record.result.get("ok"):
                return record.result
        return None


async def run_tool_agent(
    llm: LLMClient,
    *,
    agent_name: str,
    model: str,
    system: str,
    history: list[dict],
    user_message: str,
    ctx: ToolContext,
    config: BrandConfig,
    creator: CreatorProfile,
) -> AgentRun:
    """Run an LLM + tools loop until the model replies without calling a tool."""
    tools = tool_definitions(agent_name)
    messages: list[dict[str, Any]] = [
        *history,
        prompts.creator_turn(user_message),
    ]
    records: list[ToolCallRecord] = []
    last_model = model
    force_tools = False

    for iteration in range(MAX_ITERATIONS):
        result = await llm.complete(
            model=model,
            system=system,
            messages=messages,
            agent=agent_name,
            tools=tools,
            tool_choice="required" if force_tools else "auto",
            temperature=0.2,
            max_tokens=900,
        )
        last_model = result.model

        if not result.tool_calls:
            missing = _missing_tools(agent_name, records)
            if missing and iteration < MAX_ITERATIONS - 1:
                force_tools = True
                messages.append({"role": "user", "content": _nudge(missing, first=iteration == 0)})
                continue
            text = (result.text or "").strip() or FALLBACK_REPLY
            return AgentRun(text=text, tool_calls=records, model=last_model)

        force_tools = False
        messages.append(
            {
                "role": "assistant",
                "content": result.text or "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments},
                    }
                    for call in result.tool_calls
                ],
            }
        )

        for call in result.tool_calls:
            outcome = await execute_tool(call.name, call.arguments, ctx)
            records.append(
                ToolCallRecord(
                    name=call.name,
                    arguments=_safe_args(call.arguments),
                    result=outcome,
                )
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": json.dumps(outcome, default=str)[:8000],
                }
            )

    # Out of iterations: force a plain-text summary grounded in what happened.
    messages.append(
        {
            "role": "user",
            "content": (
                "Stop calling tools. Reply to the creator now, in plain text, describing the "
                "outcome that the tool results show. Do not invent anything."
            ),
        }
    )
    final = await llm.complete(
        model=model,
        system=system,
        messages=messages,
        agent=agent_name,
        temperature=0.2,
        max_tokens=600,
    )
    return AgentRun(
        text=(final.text or "").strip() or FALLBACK_REPLY,
        tool_calls=records,
        model=final.model,
    )


def _nudge(missing: list[str], *, first: bool) -> str:
    intro = (
        "You answered without calling the tools you were given. "
        if first
        else "You still have not called every required tool. "
    )
    return (
        intro
        + "The creator in this conversation is already identified -- never ask them for their id, "
        + "handle, or metrics. Call these tools now: "
        + ", ".join(missing)
        + "."
    )


def _safe_args(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except (json.JSONDecodeError, ValueError):
        return {"_raw": raw}
    return parsed if isinstance(parsed, dict) else {"_raw": raw}
