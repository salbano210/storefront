"""Redaction unit tests (no async needed)."""

from __future__ import annotations

from app.services.redactor import REDACTED, contains_secret, redact, redact_text

FAKE_KEY = "sk-or-v1-SUPERSECRETKEYVALUEFORTESTS0001"


def test_redactor_scrubs_nested_payloads():
    payload = {
        "headers": {"Authorization": f"Bearer {FAKE_KEY}"},
        "api_key": FAKE_KEY,
        "nested": [{"note": f"key was {FAKE_KEY} ok"}],
    }
    cleaned = redact(payload)
    assert not contains_secret(cleaned)
    assert cleaned["api_key"] == REDACTED
    assert FAKE_KEY not in cleaned["nested"][0]["note"]


def test_redact_text_handles_bearer_tokens():
    assert "abc123" not in redact_text("Authorization: Bearer abc123456789012345678")


def test_contains_secret_detects_leaks():
    assert contains_secret({"note": FAKE_KEY}) is True
    assert contains_secret({"note": "nothing to see"}) is False
