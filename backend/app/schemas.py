"""Pydantic schemas: the validated brand configuration plus API payloads.

The configuration schema is the contract between the admin UI, the persistence
layer and the agents. Everything the agents use to make decisions is validated
here before it can be saved.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import metrics

# The intent agent decides whether a message is something we have an agent for.
# There is one specialist (gifting). Everything else is out of scope and goes to a
# person -- there is no general-purpose agent to fall back on.
Intent = Literal["gift_request", "out_of_scope"]

# Bumped when the shipped configuration gains a field an already-stored
# configuration has to pick up (see ``BrandConfig.schema_version``).
CONFIG_SCHEMA_VERSION = 6

AGENT_KEYS = ("router", "gift_request")


# ---------------------------------------------------------------------------
# Brand configuration
# ---------------------------------------------------------------------------
class BrandInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field("Summer Soft Skincare", min_length=1, max_length=80)
    description: str = Field("A premium skincare brand", max_length=400)
    positioning: str = Field("", max_length=600)
    tone: str = Field("warm, concise, professional", max_length=200)


class CriterionRule(BaseModel):
    """One admin-configured condition, drawn from the verified-metric registry.

    Examples::

        {"metric": "followers",  "operator": "gte",          "value": 50000}
        {"metric": "categories", "operator": "includes_any", "value": ["beauty"]}

    Validation happens here, so an unknown metric or a bad operator is a 422 at
    save time rather than a surprise mid-conversation.
    """

    model_config = ConfigDict(extra="forbid")

    metric: str = Field(min_length=1, max_length=48)
    operator: str = Field(min_length=1, max_length=24)
    value: Any = None
    label: str = Field("", max_length=80)

    @model_validator(mode="after")
    def _validate_against_registry(self) -> "CriterionRule":
        metrics.validate_rule(self.metric, self.operator, self.value)
        self.value = metrics.normalise_value(
            metrics.get_metric(self.metric), self.value
        )
        return self

    def describe(self) -> str:
        return metrics.describe_rule(self.metric, self.operator, self.value)


class GiftOffer(BaseModel):
    """A gift the brand will send, with its own eligibility criteria.

    Criteria are an AND. An empty list means every creator qualifies, which is a
    legitimate configuration for a low-cost sample.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    sku: str = Field(min_length=1, max_length=64)
    description: str = Field("", max_length=400)
    url: str = Field("", max_length=500)
    retail_price: float = Field(0.0, ge=0)
    tags: list[str] = Field(default_factory=list)
    criteria: list[CriterionRule] = Field(default_factory=list)

    @field_validator("sku")
    @classmethod
    def _normalise_sku(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("id")
    @classmethod
    def _normalise_id(cls, value: str) -> str:
        return value.strip().lower().replace(" ", "-")

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: list[str]) -> list[str]:
        return [tag.strip().lower() for tag in value if tag.strip()]

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        value = value.strip()
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return value


class GiftingConfig(BaseModel):
    """The brand's gift offers.

    Each offer carries its *complete* criteria -- there is no brand-level gate, so
    what a creator can receive is decided in exactly one place per gift.
    """

    model_config = ConfigDict(extra="forbid")

    offers: list[GiftOffer] = Field(default_factory=list)
    notes: str = Field("", max_length=600)

    @model_validator(mode="after")
    def _unique_offers(self) -> "GiftingConfig":
        if not self.offers:
            raise ValueError("at least one gift offer must be configured")
        ids = [offer.id for offer in self.offers]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate gift ids in offers")
        skus = [offer.sku for offer in self.offers]
        if len(skus) != len(set(skus)):
            raise ValueError("duplicate SKUs in offers")
        return self

    def resolve(self, identifier: str) -> GiftOffer | None:
        """Find an offer by id, SKU or name.

        Models reliably pass *whichever* identifier they noticed in the previous
        tool result -- often the SKU. Refusing those turns a working conversation
        into a dead end, so accept all three (case-insensitively).
        """
        wanted = (identifier or "").strip().lower()
        if not wanted:
            return None
        for offer in self.offers:
            if offer.id == wanted or offer.sku.lower() == wanted or offer.name.lower() == wanted:
                return offer
        return None

    def find(self, sku: str) -> GiftOffer | None:
        return self.resolve(sku)


class EscalationRule(CriterionRule):
    """A verified-metric condition that hands the conversation to a human.

    Same rule language as gift criteria -- metric, operator, value -- plus the
    reason the human is handed with it. Evaluated in Python on every turn, so a
    flagged creator is escalated on the first message rather than on the third.
    """

    reason: str = Field("", max_length=200)


class EscalationKeyword(BaseModel):
    """A phrase in the creator's own message that hands the conversation to a human.

    Matched case-insensitively as whole words, with any suffix allowed on the last
    one: "refund" catches "refunds" and "refunded", "lawyer" catches "lawyers", and
    "sue" does not catch "issue".
    """

    model_config = ConfigDict(extra="forbid")

    phrase: str = Field(min_length=2, max_length=80)
    reason: str = Field("", max_length=200)

    @field_validator("phrase")
    @classmethod
    def _tidy(cls, value: str) -> str:
        return " ".join(value.split()).strip()


class EscalationConfig(BaseModel):
    """When to take a conversation off the agent and leave it to a person.

    Two independent triggers, either of which escalates:

    * ``keywords`` -- the creator's words, so "I need to speak to a human" or a
      safety complaint reaches a person without the model being involved;
    * ``metrics`` -- the verified creator profile, so an audience-quality flag or a
      VIP account is handled by a human rather than decided by an agent.

    ``reply`` is what the creator sees. The reasons are internal: they name the
    trigger for whoever picks the conversation up, and are never quoted back.
    """

    model_config = ConfigDict(extra="forbid")

    keywords: list[EscalationKeyword] = Field(default_factory=list)
    metrics: list[EscalationRule] = Field(default_factory=list)
    reply: str = Field(
        (
            "Thanks for flagging that — I'm passing this conversation to a member of the team, "
            "who will pick it up from here. You don't need to do anything else."
        ),
        max_length=600,
    )
    notes: str = Field("", max_length=600)

    @model_validator(mode="after")
    def _unique_keywords(self) -> "EscalationConfig":
        phrases = [item.phrase.lower() for item in self.keywords]
        if len(phrases) != len(set(phrases)):
            raise ValueError("duplicate escalation keywords")
        return self

    def describe(self) -> list[str]:
        """Plain-English triggers, for the prompt and the admin UI."""
        out = [f'keyword: "{item.phrase}"' for item in self.keywords]
        out += [f"metric: {item.describe()}" for item in self.metrics]
        return out


class AgentInstruction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instructions: str = Field("", max_length=2000)
    constraints: str = Field("", max_length=2000)


class AgentsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    router: AgentInstruction = Field(default_factory=AgentInstruction)
    gift_request: AgentInstruction = Field(default_factory=AgentInstruction)


class ModelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    router: str = Field("google/gemini-2.5-flash-lite", min_length=3)
    gift_request: str = Field("google/gemini-2.5-flash-lite", min_length=3)
    fallback: str = Field("openai/gpt-4o-mini", min_length=3)


class BrandConfig(BaseModel):
    """The full, validated brand configuration."""

    model_config = ConfigDict(extra="forbid")

    # Bumped when the shipped defaults change in a way an already-stored
    # configuration needs to pick up. A stored config with an older marker is
    # reseeded from the repo defaults rather than silently missing the new fields.
    schema_version: int = Field(CONFIG_SCHEMA_VERSION, ge=1)

    brand: BrandInfo = Field(default_factory=BrandInfo)
    gifting: GiftingConfig = Field(default_factory=GiftingConfig)
    escalation: EscalationConfig = Field(default_factory=EscalationConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)


class ConfigUpdateRequest(BaseModel):
    config: BrandConfig
    expected_version: int | None = None
    updated_by: str | None = Field(None, max_length=64)


class ConfigEnvelope(BaseModel):
    version: int
    config: BrandConfig
    defaults: BrandConfig


class CreatorCard(BaseModel):
    """Public creator fields -- safe to hand to the browser."""

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    handle: str = Field(max_length=40)
    image: str = Field(default="", max_length=500)
    initials: str = Field(default="", max_length=4)
    followers: int = Field(0, ge=0, le=1_000_000_000)
    engagement_rate: float = Field(0.0, ge=0, le=1)
    categories: list[str] = Field(default_factory=list)
    primary_age_range: str = Field("", max_length=20)
    content_style: str = Field("", max_length=300)
    tagline: str = Field("", max_length=160)
    bio: str = Field("", max_length=600)


class CreatorProfile(CreatorCard):
    """Verified profile -- the authoritative data the agents decide on."""

    audience: dict[str, Any] = Field(default_factory=dict)
    brand_attributed_revenue: float = Field(0.0, ge=0)
    storefront_sales: float = Field(0.0, ge=0)
    previous_gifts: list[dict[str, Any]] = Field(default_factory=list)
    shipping_address: str = Field("", max_length=300)


class ChatRequest(BaseModel):
    creator_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(None, max_length=64)


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str
    agent: str
    # Empty when the intent agent never ran -- an escalation is decided before it,
    # so the honest answer is "nothing was classified" rather than inventing one.
    intent: Intent | Literal[""] = ""
    confidence: float
    outcome: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class KeyTestRequest(BaseModel):
    key: str | None = None


class EventOut(BaseModel):
    id: int
    session_id: str
    conversation_id: str | None
    creator_id: str | None
    agent: str | None
    type: str
    status: str
    summary: str | None
    payload: dict[str, Any] | None
    model: str | None
    latency_ms: int | None
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float | None
    config_version: int | None
    created_at: str



