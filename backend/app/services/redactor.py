"""Secret scrubbing.

Nothing that looks like an API key is ever written to the database, the
console, or an event payload. Visitors can supply their own OpenRouter key, so
this is a hard requirement, not a nicety.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED_OPENROUTER_KEY]"

_PATTERNS = (
    re.compile(r"sk-or-v1-[A-Za-z0-9\-_]{8,}"),
    re.compile(r"sk-or-[A-Za-z0-9\-_]{8,}"),
    re.compile(r"sk-[A-Za-z0-9\-_]{20,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9\-_.]{16,}"),
)

_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "openrouter_api_key",
    "x-openrouter-key",
    "key",
}


def redact_text(value: str) -> str:
    for pattern in _PATTERNS:
        value = pattern.sub(REDACTED, value)
    return value


def redact(value: Any) -> Any:
    """Recursively scrub secrets from any JSON-ish structure."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        cleaned: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in _SENSITIVE_KEYS and item:
                cleaned[key] = REDACTED
            else:
                cleaned[key] = redact(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


def contains_secret(value: Any) -> bool:
    """Used by tests to prove no key ever lands in the audit log."""
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in _PATTERNS)
    if isinstance(value, dict):
        return any(contains_secret(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_secret(item) for item in value)
    return False
