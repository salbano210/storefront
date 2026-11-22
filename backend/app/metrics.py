"""The verified-metric registry, and the rule language built on top of it.

Everything an administrator can configure as a *condition* is expressed as a rule
drawn from this registry::

    {"metric": "followers", "operator": "gte", "value": 50000}

Rules are validated when the configuration is saved -- so a typo is a 422 at save
time rather than a surprise at conversation time -- and evaluated here against a
verified creator profile. No LLM is involved in either step. That is the point of
the demo: the admin picks the metrics, this module decides.

This module deliberately imports nothing from the application. It is the lowest
layer, so ``schemas`` can depend on it without a cycle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

_AGE_RE = re.compile(r"(\d{1,2})\s*[-\u2013]\s*(\d{1,2})")

# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------
NUMERIC_OPERATORS = ("gte", "lte", "gt", "lt", "eq", "neq", "between")
LIST_OPERATORS = ("includes_any", "includes_all", "excludes")
RANGE_OPERATORS = ("overlaps", "eq")
STRING_OPERATORS = ("equals", "in")

OPERATOR_LABELS: dict[str, str] = {
    "gte": "is at least",
    "lte": "is at most",
    "gt": "is more than",
    "lt": "is less than",
    "eq": "equals",
    "neq": "does not equal",
    "between": "is between",
    "includes_any": "includes any of",
    "includes_all": "includes all of",
    "excludes": "excludes",
    "equals": "is",
    "in": "is one of",
    "overlaps": "overlaps",
}

_SHAPE_OPERATORS: dict[str, tuple[str, ...]] = {
    "numeric": NUMERIC_OPERATORS,
    "list": LIST_OPERATORS,
    "range": RANGE_OPERATORS,
    "string": STRING_OPERATORS,
}

_KIND_SHAPE: dict[str, str] = {
    "number": "numeric",
    "percent": "numeric",
    "money": "numeric",
    "list": "list",
    "range": "range",
    "string": "string",
}


def _age_range(value: str) -> tuple[int, int] | None:
    """Parse an age band such as '25-34' or '25-34' into a numeric range."""
    match = _AGE_RE.search(value or "")
    if not match:
        return None
    low, high = int(match.group(1)), int(match.group(2))
    return (min(low, high), max(low, high))


# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Metric:
    """One piece of verified creator data the admin can build rules from."""

    key: str
    label: str
    kind: str  # number | percent | money | list | range | string
    getter: Callable[[Any], Any]
    description: str = ""
    scorable: bool = True  # usable as a pitch-scoring input
    default_ideal: Any = None
    value_options: tuple[str, ...] = field(default=())

    @property
    def shape(self) -> str:
        return _KIND_SHAPE[self.kind]

    @property
    def operators(self) -> tuple[str, ...]:
        return _SHAPE_OPERATORS[self.shape]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "shape": self.shape,
            "description": self.description,
            "scorable": self.scorable,
            "default_ideal": self.default_ideal,
            "value_options": list(self.value_options),
            "operators": [
                {"value": op, "label": OPERATOR_LABELS[op]} for op in self.operators
            ],
        }


def _audience(profile: Any) -> Mapping[str, Any]:
    value = getattr(profile, "audience", None) or {}
    return value if isinstance(value, Mapping) else {}


METRICS: tuple[Metric, ...] = (
    Metric(
        key="followers",
        label="Followers",
        kind="number",
        getter=lambda p: getattr(p, "followers", 0),
        description="Verified follower count on the creator's primary channel.",
        default_ideal=50000,
    ),
    Metric(
        key="engagement_rate",
        label="Engagement rate",
        kind="percent",
        getter=lambda p: getattr(p, "engagement_rate", 0.0),
        description="Verified engagement rate. Stored as a decimal (0.03 = 3%).",
        default_ideal=0.05,
    ),
    Metric(
        key="brand_attributed_revenue",
        label="Brand-attributed revenue",
        kind="money",
        getter=lambda p: getattr(p, "brand_attributed_revenue", 0.0),
        description="Revenue this creator has already driven for the brand.",
        default_ideal=10000,
    ),
    Metric(
        key="storefront_sales",
        label="Historical Storefront sales",
        kind="money",
        getter=lambda p: getattr(p, "storefront_sales", 0.0),
        description="Lifetime sales the creator has generated via their storefront.",
        default_ideal=25000,
    ),
    Metric(
        key="previous_gift_count",
        label="Previous gifts received",
        kind="number",
        getter=lambda p: len(getattr(p, "previous_gifts", None) or []),
        description="How many gifts the brand has already sent this creator.",
        default_ideal=2,
    ),
    Metric(
        key="categories",
        label="Content categories",
        kind="list",
        getter=lambda p: list(getattr(p, "categories", None) or []),
        description="Topics the creator publishes about.",
        default_ideal=["beauty", "skincare"],
    ),
    Metric(
        key="primary_age_range",
        label="Primary audience age",
        kind="range",
        getter=lambda p: getattr(p, "primary_age_range", "") or "",
        description="Dominant age band of the creator's audience.",
        scorable=False,
        value_options=("18-24", "25-34", "35-44", "45+"),
    ),
    Metric(
        key="audience_gender",
        label="Audience top gender",
        kind="string",
        getter=lambda p: _audience(p).get("top_gender", "") or "",
        description="Gender the creator's audience skews towards.",
        scorable=False,
        value_options=("female", "male", "mixed"),
    ),
)

METRICS_BY_KEY: dict[str, Metric] = {metric.key: metric for metric in METRICS}
METRIC_KEYS: tuple[str, ...] = tuple(METRICS_BY_KEY)

SCORABLE_METRICS: tuple[Metric, ...] = tuple(m for m in METRICS if m.scorable)


def get_metric(key: str) -> Metric:
    try:
        return METRICS_BY_KEY[key]
    except KeyError:
        raise ValueError(
            f"unknown metric '{key}'. Available: {', '.join(METRIC_KEYS)}"
        ) from None


def metric_catalog() -> list[dict[str, Any]]:
    """Serialisable registry -- this is what populates the admin dropdowns."""
    return [metric.to_dict() for metric in METRICS]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
def fmt_value(metric: Metric, value: Any) -> str:
    if value is None or value == "":
        return "not on file"
    if metric.kind == "number":
        return f"{int(value):,}"
    if metric.kind == "percent":
        return f"{float(value) * 100:.1f}%"
    if metric.kind == "money":
        return f"${float(value):,.0f}"
    if metric.kind == "list":
        items = [str(item) for item in value]
        return ", ".join(items) if items else "none"
    return str(value)


def fmt_int(value: float | int) -> str:
    return f"{int(value):,}"


def fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def fmt_money(value: float) -> str:
    return f"${value:,.0f}"


def _as_number(value: Any, metric: Metric) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"'{metric.key}' expects a number, got {value!r}") from None


def _as_list(value: Any, metric: Metric) -> list[str]:
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",") if part.strip()]
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"'{metric.key}' expects a non-empty list of values")
    return [str(item).strip().lower() for item in value]


# ---------------------------------------------------------------------------
# Validation (used when the admin saves a configuration)
# ---------------------------------------------------------------------------
def validate_rule(metric_key: str, operator: str, value: Any) -> None:
    """Raise ValueError with a human-readable message if the rule cannot work."""
    metric = get_metric(metric_key)

    if operator not in metric.operators:
        raise ValueError(
            f"operator '{operator}' is not valid for '{metric.key}' "
            f"({metric.shape}). Valid operators: {', '.join(metric.operators)}"
        )

    if metric.shape == "numeric":
        if operator == "between":
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                raise ValueError(f"'{metric.key}' between expects [low, high]")
            low, high = _as_number(value[0], metric), _as_number(value[1], metric)
            if low > high:
                raise ValueError(f"'{metric.key}' between expects low <= high")
            return
        _as_number(value, metric)
        if metric.kind == "percent" and not 0 <= float(value) <= 1:
            raise ValueError(
                f"'{metric.key}' is a percentage stored as a decimal fraction "
                f"-- use 0.03 for 3%, not 3"
            )
        return

    if metric.shape == "list":
        _as_list(value, metric)
        return

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{metric.key}' expects a non-empty text value")
    if metric.shape == "range" and not _age_range(value):
        raise ValueError(
            f"'{metric.key}' expects an age band such as '25-34', got {value!r}"
        )


def normalise_value(metric: Metric, value: Any) -> Any:
    """Canonical storage form for a rule's value."""
    if metric.shape == "list":
        return _as_list(value, metric)
    if metric.shape == "numeric":
        if isinstance(value, (list, tuple)):
            return [float(item) for item in value]
        return float(value)
    return str(value).strip()


# ---------------------------------------------------------------------------
# Evaluation (used by the tools, never by the model)
# ---------------------------------------------------------------------------
def _compare(metric: Metric, operator: str, actual: Any, required: Any) -> bool:
    if metric.shape == "numeric":
        left = _as_number(actual, metric)
        if operator == "between":
            low, high = float(required[0]), float(required[1])
            return low - 1e-9 <= left <= high + 1e-9
        right = float(required)
        if operator == "gte":
            return left >= right - 1e-9
        if operator == "lte":
            return left <= right + 1e-9
        if operator == "gt":
            return left > right
        if operator == "lt":
            return left < right
        if operator == "eq":
            return abs(left - right) < 1e-9
        return abs(left - right) >= 1e-9  # neq

    if metric.shape == "list":
        have = {str(item).strip().lower() for item in (actual or [])}
        want = {str(item).strip().lower() for item in required}
        if operator == "includes_any":
            return bool(have & want)
        if operator == "includes_all":
            return want <= have
        return not (have & want)  # excludes

    if metric.shape == "range":
        if operator == "eq":
            return _age_range(str(actual)) == _age_range(str(required))
        wanted, have = _age_range(str(required)), _age_range(str(actual))
        if not wanted or not have:
            return True  # unknown on either side -> do not penalise
        return not (have[1] < wanted[0] or have[0] > wanted[1])  # overlaps

    left = str(actual or "").strip().lower()
    if operator == "equals":
        return left == str(required).strip().lower()
    return left in {str(item).strip().lower() for item in required}  # in


def describe_rule(metric_key: str, operator: str, value: Any) -> str:
    """Plain-English form, for prompts and the admin UI."""
    metric = get_metric(metric_key)
    phrase = OPERATOR_LABELS[operator]
    if metric.shape == "list":
        shown = ", ".join(str(item) for item in value)
    elif operator == "between":
        shown = f"{fmt_value(metric, value[0])} and {fmt_value(metric, value[1])}"
    else:
        shown = fmt_value(metric, value)
    return f"{metric.label} {phrase} {shown}"


def evaluate_rule(rule: Mapping[str, Any], profile: Any) -> dict[str, Any]:
    """Evaluate one rule against a verified profile. Returns a display-ready check."""
    metric = get_metric(rule["metric"])
    operator = rule["operator"]
    required = rule.get("value")
    actual = metric.getter(profile)
    passed = _compare(metric, operator, actual, required)

    if metric.shape == "list":
        required_display = ", ".join(str(item) for item in required)
    elif operator == "between":
        required_display = (
            f"{fmt_value(metric, required[0])} to {fmt_value(metric, required[1])}"
        )
    else:
        required_display = fmt_value(metric, required)

    return {
        "criterion": metric.key,  # kept for the criteria table in the UI
        "metric": metric.key,
        "label": rule.get("label") or metric.label,
        "operator": operator,
        "required": required,
        "required_display": required_display,
        "actual": actual,
        "actual_display": fmt_value(metric, actual),
        "passed": passed,
        "source": "verified profile",
        "detail": f"{metric.label} {OPERATOR_LABELS[operator]} {required_display}",
        "summary": (
            fmt_value(metric, actual)
            if passed
            else f"{fmt_value(metric, actual)} (required {required_display})"
        ),
    }


def evaluate_rules(rules: Iterable[Mapping[str, Any]], profile: Any) -> dict[str, Any]:
    """Evaluate a rule set as an AND. An empty rule set passes (open to everyone)."""
    checks = [evaluate_rule(rule, profile) for rule in rules]
    failed = [check for check in checks if not check["passed"]]
    return {
        "eligible": not failed,
        "checks": checks,
        "failed": [check["criterion"] for check in failed],
        "failed_labels": [check["label"] for check in failed],
        "shortfalls": [f"{check['label']}: {check['summary']}" for check in failed],
    }
