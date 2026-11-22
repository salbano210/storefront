"""Pydantic schemas: the validated brand configuration plus API payloads.

The configuration schema is the contract between the admin UI, the persistence
layer and the agents. Everything the agents use to make decisions is validated
here before it can be saved.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Intent = Literal["gift_request", "creator_pitch", "general_inquiry"]

AGENT_KEYS = ("router", "gift_request", "creator_pitch", "general_inquiry")
WEIGHT_KEYS = ("category", "engagement", "followers", "brand_revenue", "shopmy_sales")


# ---------------------------------------------------------------------------
# Brand configuration
# ---------------------------------------------------------------------------
class BrandInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field("Demo Brand", min_length=1, max_length=80)
    description: str = Field("A premium skincare brand", max_length=400)
    positioning: str = Field("", max_length=600)
    tone: str = Field("warm, concise, professional", max_length=200)


class GiftingCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_followers: int = Field(10000, ge=0, le=100_000_000)
    minimum_engagement_rate: float = Field(0.03, ge=0, le=1)
    minimum_brand_revenue: float = Field(500, ge=0)
    required_categories: list[str] = Field(default_factory=list)
    notes: str = Field("", max_length=600)

    @field_validator("required_categories")
    @classmethod
    def _clean_categories(cls, value: list[str]) -> list[str]:
        return [c.strip().lower() for c in value if c.strip()]


class EligibleProduct(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    sku: str = Field(min_length=1, max_length=64)
    description: str = Field("", max_length=400)
    eligible: bool = True
    retail_price: float = Field(0.0, ge=0)

    @field_validator("sku")
    @classmethod
    def _normalise_sku(cls, value: str) -> str:
        return value.strip().upper()


class GiftingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criteria: GiftingCriteria = Field(default_factory=GiftingCriteria)
    eligible_products: list[EligibleProduct] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_skus(self) -> "GiftingConfig":
        skus = [p.sku for p in self.eligible_products]
        if len(skus) != len(set(skus)):
            raise ValueError("duplicate SKUs in eligible_products")
        if not any(p.eligible for p in self.eligible_products):
            raise ValueError("at least one product must be eligible for gifting")
        return self


class PitchCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_categories: list[str] = Field(default_factory=lambda: ["beauty", "skincare"])
    minimum_engagement_rate: float = Field(0.025, ge=0, le=1)
    minimum_followers: int = Field(0, ge=0)
    minimum_brand_revenue: float = Field(0, ge=0)
    preferred_audience_age: str = Field("22-35", max_length=20)

    @field_validator("preferred_categories")
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        return [c.strip().lower() for c in value if c.strip()]


class PitchTargets(BaseModel):
    """'Ideal' values a creator is scored against (component = actual / ideal, capped at 1)."""

    model_config = ConfigDict(extra="forbid")

    ideal_followers: int = Field(50000, gt=0)
    ideal_engagement_rate: float = Field(0.05, gt=0, le=1)
    ideal_brand_revenue: float = Field(10000, gt=0)
    ideal_shopmy_sales: float = Field(25000, gt=0)


class LabelThreshold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=64)
    min_score: float = Field(0, ge=0, le=100)


class PitchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criteria: PitchCriteria = Field(default_factory=PitchCriteria)
    classification_labels: list[LabelThreshold] = Field(default_factory=list)
    targets: PitchTargets = Field(default_factory=PitchTargets)
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "category": 30,
            "engagement": 25,
            "followers": 20,
            "brand_revenue": 15,
            "shopmy_sales": 10,
        }
    )

    @model_validator(mode="after")
    def _validate_pitch(self) -> "PitchConfig":
        labels = [item.label for item in self.classification_labels]
        if not labels:
            raise ValueError("classification_labels cannot be empty")
        if len(labels) != len(set(labels)):
            raise ValueError("classification_labels must be unique")
        # Highest threshold first so label lookup is a simple scan.
        self.classification_labels = sorted(
            self.classification_labels, key=lambda item: item.min_score, reverse=True
        )
        if min(item.min_score for item in self.classification_labels) > 0:
            raise ValueError("one classification label must have min_score 0 (the fallback)")
        missing = [key for key in WEIGHT_KEYS if key not in self.weights]
        if missing:
            raise ValueError(f"weights missing keys: {', '.join(missing)}")
        unknown = [key for key in self.weights if key not in WEIGHT_KEYS]
        if unknown:
            raise ValueError(f"weights has unknown keys: {', '.join(unknown)}")
        if sum(self.weights.values()) <= 0:
            raise ValueError("weights must sum to a positive number")
        return self


class AgentInstruction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instructions: str = Field("", max_length=2000)
    constraints: str = Field("", max_length=2000)


class AgentsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    router: AgentInstruction = Field(default_factory=AgentInstruction)
    gift_request: AgentInstruction = Field(default_factory=AgentInstruction)
    creator_pitch: AgentInstruction = Field(default_factory=AgentInstruction)
    general_inquiry: AgentInstruction = Field(default_factory=AgentInstruction)


class ModelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    router: str = Field("google/gemini-2.5-flash-lite", min_length=3)
    gift_request: str = Field("google/gemini-2.5-flash-lite", min_length=3)
    creator_pitch: str = Field("google/gemini-2.5-flash-lite", min_length=3)
    general_inquiry: str = Field("meta-llama/llama-3.1-8b-instruct", min_length=3)
    fallback: str = Field("openai/gpt-4.1-nano", min_length=3)


class BrandConfig(BaseModel):
    """The full, validated brand configuration."""

    model_config = ConfigDict(extra="forbid")

    brand: BrandInfo = Field(default_factory=BrandInfo)
    gifting: GiftingConfig = Field(default_factory=GiftingConfig)
    pitch: PitchConfig = Field(default_factory=PitchConfig)
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

    id: str
    name: str
    handle: str
    image: str
    initials: str
    followers: int
    engagement_rate: float
    categories: list[str]
    primary_age_range: str
    content_style: str
    tagline: str
    bio: str


class CreatorProfile(CreatorCard):
    """Verified profile -- the authoritative data the agents decide on."""

    audience: dict[str, Any]
    brand_attributed_revenue: float
    shopmy_sales: float
    previous_gifts: list[dict[str, Any]]
    shipping_address: str


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



