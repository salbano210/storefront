"""LLM client error handling: fallback attempts and out-of-credits detection."""

from __future__ import annotations

import pytest

from app.services import llm_client
from app.services.llm_client import LLMCreditsExhausted, LLMError, LLMClient

pytestmark = pytest.mark.asyncio

MESSAGES = [{"role": "user", "content": "hi"}]


class FakeStatusError(Exception):
    """Mimics openai.APIStatusError: carries the HTTP status code."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class StubCompletions:
    def __init__(self, error: Exception, calls: list[str]) -> None:
        self._error = error
        self._calls = calls

    async def create(self, **kwargs):
        self._calls.append(kwargs["model"])
        raise self._error


class StubChat:
    def __init__(self, error: Exception, calls: list[str]) -> None:
        self.completions = StubCompletions(error, calls)


class StubClient:
    def __init__(self, error: Exception, calls: list[str]) -> None:
        self.chat = StubChat(error, calls)


def _client_with_error(error: Exception, fallback: str = "fallback/model") -> tuple[LLMClient, list[str]]:
    client = LLMClient("sk-or-v1-test-key", logger=None, fallback_model=fallback)
    calls: list[str] = []
    client._client = StubClient(error, calls)  # noqa: SLF001 - deliberate test seam
    return client, calls


async def test_credit_errors_raise_a_distinct_exception():
    client, calls = _client_with_error(
        FakeStatusError(402, "This request requires more credits")
    )
    with pytest.raises(LLMCreditsExhausted):
        await client.complete(model="primary/model", system="s", messages=MESSAGES)
    # the fallback was attempted before giving up
    assert calls == ["primary/model", "fallback/model"]


async def test_credit_limit_wording_is_also_detected():
    client, _ = _client_with_error(Exception("You have hit your credit limit"))
    with pytest.raises(LLMCreditsExhausted):
        await client.complete(model="primary/model", system="s", messages=MESSAGES)


async def test_other_errors_stay_plain_llm_errors():
    client, calls = _client_with_error(FakeStatusError(500, "upstream exploded"))
    with pytest.raises(LLMError) as excinfo:
        await client.complete(model="primary/model", system="s", messages=MESSAGES)
    assert not isinstance(excinfo.value, LLMCreditsExhausted)
    assert calls == ["primary/model", "fallback/model"]


async def test_single_attempt_when_model_equals_fallback():
    client, calls = _client_with_error(FakeStatusError(402, "insufficient credits"), fallback="only/model")
    with pytest.raises(LLMCreditsExhausted):
        await client.complete(model="only/model", system="s", messages=MESSAGES)
    assert calls == ["only/model"]


async def test_chat_surfaces_exhausted_credits_clearly(client, monkeypatch):
    def factory(api_key, *, logger=None, fallback_model=None):
        instance = LLMClient(api_key, logger=logger, fallback_model=fallback_model)
        instance._client = StubClient(  # noqa: SLF001 - deliberate test seam
            FakeStatusError(402, "This request requires more credits"), []
        )
        return instance

    monkeypatch.setattr(llm_client, "build_llm", factory)

    response = await client.post(
        "/api/chat", json={"creator_id": "creator_001", "message": "Can I get the serum?"}
    )
    assert response.status_code == 402
    detail = response.json()["detail"]
    assert detail["code"] == "credits_exhausted"
    assert "run out of credits" in detail["message"]

    # the failure is still visible in the audit log
    events = (await client.get("/api/logs", params={"event_type": "llm_error"})).json()
    assert events["count"] >= 1
    assert events["events"][0]["status"] == "error"
