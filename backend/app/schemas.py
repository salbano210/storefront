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
# There is one specialist (gifting) plus the front desk that answers the rest.
Intent = Literal["gift_request", "general_inquiry"]

AGENT_KEYS = ("router", "gift_request", "general_inquiry")


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


class AgentInstruction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instructions: str = Field("", max_length=2000)
    constraints: str = Field("", max_length=2000)


class AgentsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    router: AgentInstruction = Field(default_factory=AgentInstruction)
    gift_request: AgentInstruction = Field(default_factory=AgentInstruction)
    general_inquiry: AgentInstruction = Field(default_factory=AgentInstruction)


class ModelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    router: str = Field("google/gemini-2.5-flash-lite", min_length=3)
    gift_request: str = Field("google/gemini-2.5-flash-lite", min_length=3)
    general_inquiry: str = Field("meta-llama/llama-3.1-8b-instruct", min_length=3)
    fallback: str = Field("openai/gpt-4.1-nano", min_length=3)


class BrandConfig(BaseModel):
    """The full, validated brand configuration."""

    model_config = ConfigDict(extra="forbid")

    brand: BrandInfo = Field(default_factory=BrandInfo)
    gifting: GiftingConfig = Field(default_factory=GiftingConfig)
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
    intent: Intent
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



