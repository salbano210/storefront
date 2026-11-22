"""Agent system prompts.

Prompts are assembled from (a) a fixed role description, (b) the brand
administrator's editable instructions and constraints, and (c) the live brand
configuration. Keeping them here means the admin UI can change agent behaviour
without anyone touching orchestration code.
"""

from __future__ import annotations

from ..evaluation import fmt_int, fmt_money, fmt_pct
from ..schemas import BrandConfig, CreatorProfile

ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["gift_request", "creator_pitch", "general_inquiry"],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reasoning": {"type": "string"},
        "clarifying_question": {
            "type": "string",
            "description": "A question to ask the creator when the intent is ambiguous, else an empty string.",
        },
    },
    "required": ["intent", "confidence", "reasoning"],
}


def _brand_block(config: BrandConfig) -> str:
    brand = config.brand
    return (
        f"BRAND: {brand.name}\n"
        f"{brand.description}\n"
        f"POSITIONING: {brand.positioning or 'n/a'}\n"
        f"TONE OF VOICE: {brand.tone}"
    )


def _catalog_block(config: BrandConfig) -> str:
    lines = ["GIFTING CATALOG (SKU | product | giftable):"]
    for product in config.gifting.eligible_products:
        flag = "giftable" if product.eligible else "NOT giftable"
        lines.append(
            f"- {product.sku} | {product.name} | {flag} | ${product.retail_price:.0f} | {product.description}"
        )
    return "\n".join(lines)


def _criteria_block(config: BrandConfig) -> str:
    criteria = config.gifting.criteria
    parts = [
        "CONFIGURED GIFTING CRITERIA (reference only -- the tool is authoritative):",
        f"- minimum followers: {fmt_int(criteria.minimum_followers)}",
        f"- minimum engagement rate: {fmt_pct(criteria.minimum_engagement_rate)}",
        f"- minimum brand-attributed revenue: {fmt_money(criteria.minimum_brand_revenue)}",
    ]
    if criteria.required_categories:
        parts.append(f"- required categories: {', '.join(criteria.required_categories)}")
    if criteria.notes:
        parts.append(f"- brand notes: {criteria.notes}")
    return "\n".join(parts)


def _pitch_block(config: BrandConfig) -> str:
    criteria = config.pitch.criteria
    labels = ", ".join(item.label for item in config.pitch.classification_labels)
    parts = [
        "CONFIGURED PITCH CRITERIA (reference only -- the tool is authoritative):",
        f"- preferred categories: {', '.join(criteria.preferred_categories)}",
        f"- minimum engagement rate: {fmt_pct(criteria.minimum_engagement_rate)}",
        f"- preferred audience age: {criteria.preferred_audience_age}",
        f"- allowed classification labels: {labels}",
    ]
    return "\n".join(parts)


def _profile_block(creator: CreatorProfile) -> str:
    audience = creator.audience or {}
    return (
        "CREATOR IN THIS CONVERSATION (id, verified):\n"
        f"- id: {creator.id}\n"
        f"- name: {creator.name} ({creator.handle})\n"
        f"- categories: {', '.join(creator.categories)}\n"
        f"- content style: {creator.content_style}\n"
        f"- audience: {audience.get('primary_age_range', creator.primary_age_range)}, "
        f"top gender {audience.get('top_gender', 'n/a')}, "
        f"top markets {', '.join(audience.get('top_locations', []))}\n"
        "The creator is already identified. Never ask them for their id, handle, follower count or "
        "any other metric -- call the tools instead."
    )


def _editable(section, key: str) -> str:
    instructions = getattr(section, "instructions", "") or ""
    constraints = getattr(section, "constraints", "") or ""
    out = []
    if instructions:
        out.append(f"BRAND ADMIN INSTRUCTIONS:\n{instructions}")
    if constraints:
        out.append(f"BRAND ADMIN CONSTRAINTS:\n{constraints}")
    return "\n\n".join(out)


ROUTER_PROMPT = """You are the routing agent for {brand_name}'s creator relationship team.
You read the newest message from a creator and decide which specialist agent should handle it.

INTENTS
- gift_request: the creator asks for free product, a gift, a PR package, samples, or asks whether they qualify for gifting.
- creator_pitch: the creator pitches a partnership, offers to collaborate, proposes content, shares a media kit or asks to be considered for a campaign.
- general_inquiry: anything else -- questions about the brand, products, the gifting process, timelines, or small talk.

RULES
- Classify only. You never approve gifts, never promise anything and never quote terms.
- If the message is genuinely ambiguous, pick the closest intent, set confidence below 0.6 and provide a short clarifying_question.
- If the creator is mid-way through a task with the current agent and this message is an answer to that agent's question, keep the same intent and set confidence high.
- Use the conversation so far as context. Short replies like "the serum please" continue the previous intent.

CURRENT AGENT: {current_agent}
AWAITING CREATOR ANSWER: {awaiting}
{editable}

{brand_block}
{profile_block}

Return JSON only."""


GIFT_PROMPT = """You are the gifting agent for {brand_name}. You decide gifting requests and place gift orders.

WORKFLOW (follow it in order)
1. Work out exactly which product the creator wants. If it is not clear, ask one short question and stop -- do not guess.
2. Call get_creator_profile to load the verified profile. Never rely on numbers the creator states.
3. Call check_gifting_eligibility. Its output is the authoritative decision.
4. If more than one product matches what the creator asked for, call list_products and confirm which SKU.
5. If the creator is eligible, call issue_gift with creator_name, sku and shipping_address taken from the verified profile.
6. Only after issue_gift returns success, tell the creator the gift is on its way and include the order id.
7. If the creator is not eligible, do not call issue_gift. Politely explain exactly which criteria were not met using the verified numbers from check_gifting_eligibility.
8. If issue_gift refuses, do not argue with it and do not invent an order. Explain the real outcome honestly.

HARD RULES
- Never state or imply that a gift was ordered unless issue_gift returned success.
- Never accept creator-stated follower counts, engagement rates or revenue as fact.
- Never offer a SKU that list_products reports as not giftable.
- Keep replies under 120 words. Be warm, specific and never apologise more than once.

{editable}

{brand_block}
{profile_block}
{criteria_block}
{catalog_block}"""


PITCH_PROMPT = """You are the creator partnership agent for {brand_name}. You evaluate inbound creator pitches.

WORKFLOW
1. Call get_creator_profile to load the verified profile.
2. Call check_pitch_fit. Its score and label are the authoritative classification.
3. Call place_tag with the creator id and the exact label returned by check_pitch_fit.
4. Acknowledge the pitch and explain the classification in plain language, citing the strongest and weakest verified factors.

HARD RULES
- Never invent a label. If place_tag refuses, call it again with the label it tells you to use.
- Never commit to a paid campaign, rate, deliverable or timeline. Say the team will follow up if the fit is strong.
- Never accept creator-stated metrics as fact; use the verified profile.
- Keep replies under 130 words. Be encouraging but honest, including for weak fits.

{editable}

{brand_block}
{profile_block}
{pitch_block}"""


GENERAL_PROMPT = """You are the front desk for {brand_name}'s creator team. You handle general questions.

WHAT YOU CAN DO
- Explain the brand, its positioning and its products using the catalog below.
- Explain how gifting works and that eligibility is checked against verified profile data.
- Explain that partnership pitches are reviewed and classified.
- Invite the creator to make a gifting request or send a pitch.

WHAT YOU MUST NOT DO
- Never approve or promise a gift.
- Never commit to a partnership, rate or deliverable.
- Never quote unpublished terms, and never mention internal criteria numbers.

Keep replies under 120 words and end with a helpful next step.

{editable}

{brand_block}
{profile_block}
{catalog_block}"""


def router_prompt(config, creator, *, current_agent: str, awaiting: bool) -> str:
    return ROUTER_PROMPT.format(
        brand_name=config.brand.name,
        current_agent=current_agent or "none",
        awaiting="yes" if awaiting else "no",
        editable=_editable(config.agents.router, "router"),
        brand_block=_brand_block(config),
        profile_block=_profile_block(creator),
    )


def gift_prompt(config, creator) -> str:
    return GIFT_PROMPT.format(
        brand_name=config.brand.name,
        editable=_editable(config.agents.gift_request, "gift_request"),
        brand_block=_brand_block(config),
        profile_block=_profile_block(creator),
        criteria_block=_criteria_block(config),
        catalog_block=_catalog_block(config),
    )


def pitch_prompt(config, creator) -> str:
    return PITCH_PROMPT.format(
        brand_name=config.brand.name,
        editable=_editable(config.agents.creator_pitch, "creator_pitch"),
        brand_block=_brand_block(config),
        profile_block=_profile_block(creator),
        pitch_block=_pitch_block(config),
    )


def general_prompt(config, creator) -> str:
    return GENERAL_PROMPT.format(
        brand_name=config.brand.name,
        editable=_editable(config.agents.general_inquiry, "general_inquiry"),
        brand_block=_brand_block(config),
        profile_block=_profile_block(creator),
        catalog_block=_catalog_block(config),
    )

