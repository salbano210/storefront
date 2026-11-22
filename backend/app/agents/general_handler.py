"""General inquiry handler -- no tools, no business actions."""

from __future__ import annotations

from ..schemas import BrandConfig, CreatorProfile
from ..services.llm_client import LLMClient
from . import prompts

AGENT_NAME = "general_handler"


async def run_general(
    llm: LLMClient,
    *,
    config: BrandConfig,
    creator: CreatorProfile,
    history: list[dict],
    user_message: str,
    clarifying_question: str = "",
) -> str:
    system = prompts.general_prompt(config, creator)
    if clarifying_question:
        system += (
            "\n\nThe creator's message was ambiguous. Ask this clarifying question naturally, "
            f"in your own words: {clarifying_question}"
        )
    result = await llm.complete(
        model=config.models.general_inquiry,
        system=system,
        messages=[*history, prompts.creator_turn(user_message)],
        agent=AGENT_NAME,
        temperature=0.3,
        max_tokens=500,
    )
    return (result.text or "").strip() or (
        "Happy to help -- are you asking about gifting, a partnership, or our products?"
    )
