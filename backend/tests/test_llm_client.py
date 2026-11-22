"""LLM client error handling: retries, fallback attempts, out-of-credits detection."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import settings
from app.services import llm_client
from app.services.llm_client import LLMCreditsExhausted, LLMError, LLMClient

pytestmark = pytest.mark.asyncio

MESSAGES = [{"role": "user", "content": "hi"}]
MAX_RETRIES = settings.OPENROUTER_MAX_RETRIES


class FakeStatusError(Exception):
    """Mimics openai.APIStatusError: carries the HTTP status code."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def _ok_response(text: str = "hello") -> SimpleNamespace:
    message = SimpleNamespace(content=text, tool_calls=[])
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=3),
        model="stub/model",
    )
    response.model_dump = lambda: {"usage": {"cost": 0.0001}}
    return response


class StubCompletions:
    """Fails `failures` times (or always, when `failures` is None), then succeeds."""

    def __init__(
        self, error: Exception | None, calls: list[str], failures: int | None = None
    ) -> None:
        self._error = error
        self._calls = calls
        self._remaining = failures

    async def create(self, **kwargs):
        self._calls.append(kwargs["model"])
        if self._error is None:
            return _ok_response()
        if self._remaining is None:
            raise self._error
        if self._remaining > 0:
            self._remaining -= 1
            raise self._error
        return _ok_response()


class StubChat:
    def __init__(self, completions: StubCompletions) -> None:
        self.completions = completions


class StubClient:
    def __init__(self, completions: StubCompletions) -> None:
        self.chat = StubChat(completions)


def _client(
    error: Exception | None = None,
    *,
    fallback: str = "fallback/model",
    failures: int | None = None,
) -> tuple[LLMClient, list[str]]:
    client = LLMClient("sk-or-v1-test-key", logger=None, fallback_model=fallback)
    calls: list[str] = []
    client._client = StubClient(StubCompletions(error, calls, failures))  # noqa: SLF001
    return client, calls


# --- retries on the same model ---------------------------------------------
async def test_transient_5xx_is_retried_on_the_same_model():
    """A provider blip must not switch models mid-conversation."""
    error = FakeStatusError(502, "Provider returned an empty response")
    client, calls = _client(error, failures=2)

    result = await client.complete(model="primary/model", system="s", messages=MESSAGES)

    assert calls == ["primary/model"] * 3  # the initial call plus two retries
    assert result.used_fallback is False
    assert result.model == "primary/model"


async def test_retries_are_bounded_and_then_the_fallback_runs():
    error = FakeStatusError(502, "Provider returned an empty response")
    client, calls = _client(error)

    with pytest.raises(LLMError):
        await client.complete(model="primary/model", system="s", messages=MESSAGES)

    attempts = MAX_RETRIES + 1
    assert calls == ["primary/model"] * attempts + ["fallback/model"] * attempts


async def test_non_retryable_errors_go_straight_to_the_fallback():
    """A 400 will never succeed on retry, so don't waste the time or the tokens."""
    client, calls = _client(FakeStatusError(400, "bad request"))
    with pytest.raises(LLMError):
        await client.complete(model="primary/model", system="s", messages=MESSAGES)
    assert calls == ["primary/model", "fallback/model"]


async def test_connection_errors_are_retried():
    class APIConnectionError(Exception):
        pass

    client, calls = _client(APIConnectionError("connection reset"), failures=1)
    result = await client.complete(model="primary/model", system="s", messages=MESSAGES)
    assert calls == ["primary/model", "primary/model"]
    assert result.used_fallback is False


# --- credit errors ---------------------------------------------------------
async def test_credit_errors_raise_a_distinct_exception():
    client, calls = _client(FakeStatusError(402, "This request requires more credits"))
    with pytest.raises(LLMCreditsExhausted):
        await client.complete(model="primary/model", system="s", messages=MESSAGES)
    # No credits means no model works, so the fallback is never spent on it.
    assert calls == ["primary/model"]


async def test_credit_limit_wording_is_also_detected():
    client, _ = _client(Exception("You have hit your credit limit"))
    with pytest.raises(LLMCreditsExhausted):
        await client.complete(model="primary/model", system="s", messages=MESSAGES)


async def test_other_errors_stay_plain_llm_errors():
    client, calls = _client(FakeStatusError(500, "upstream exploded"))
    with pytest.raises(LLMError) as excinfo:
        await client.complete(model="primary/model", system="s", messages=MESSAGES)
    assert not isinstance(excinfo.value, LLMCreditsExhausted)
    assert "fallback/model" in calls


async def test_single_attempt_when_model_equals_fallback():
    client, calls = _client(
        FakeStatusError(402, "insufficient credits"), fallback="only/model"
    )
    with pytest.raises(LLMCreditsExhausted):
        await client.complete(model="only/model", system="s", messages=MESSAGES)
    assert calls == ["only/model"]


# --- what the operator sees ------------------------------------------------
async def test_retry_that_wins_is_recorded_without_an_error_event(client, monkeypatch):
    """A blip a retry absorbs should be visible, but must not look like a failure."""

    def factory(api_key, *, logger=None, fallback_model=None):
        instance = LLMClient(api_key, logger=logger, fallback_model=fallback_model)
        instance._client = StubClient(  # noqa: SLF001 - deliberate test seam
            StubCompletions(
                FakeStatusError(502, "Provider returned an empty response"), [], failures=1
            )
        )
        return instance

    monkeypatch.setattr(llm_client, "build_llm", factory)

    response = await client.post(
        "/api/chat", json={"creator_id": "creator_001", "message": "Can I get the serum?"}
    )
    assert response.status_code == 200, response.text

    logs = (await client.get("/api/logs", params={"limit": 400})).json()["events"]
    retried = [
        event
        for event in logs
        if event["type"] == "llm_response" and event["payload"].get("retries")
    ]
    assert retried, "the absorbed retry should be recorded on the successful call"
    assert not [event for event in logs if event["type"] == "llm_fallback"]


async def test_exhausted_calls_record_how_many_attempts_were_made(client, monkeypatch):
    def factory(api_key, *, logger=None, fallback_model=None):
        instance = LLMClient(api_key, logger=logger, fallback_model=fallback_model)
        instance._client = StubClient(  # noqa: SLF001 - deliberate test seam
            StubCompletions(FakeStatusError(503, "upstream unavailable"), [])
        )
        return instance

    monkeypatch.setattr(llm_client, "build_llm", factory)

    response = await client.post(
        "/api/chat", json={"creator_id": "creator_001", "message": "Can I get the serum?"}
    )
    assert response.status_code == 502

    events = (await client.get("/api/logs", params={"event_type": "llm_error"})).json()
    assert events["count"] >= 1
    first = events["events"][0]
    assert first["status"] == "error"
    assert first["payload"]["attempts"] == MAX_RETRIES + 1


async def test_chat_surfaces_exhausted_credits_clearly(client, monkeypatch):
    def factory(api_key, *, logger=None, fallback_model=None):
        instance = LLMClient(api_key, logger=logger, fallback_model=fallback_model)
        instance._client = StubClient(  # noqa: SLF001 - deliberate test seam
            StubCompletions(FakeStatusError(402, "This request requires more credits"), [])
        )
        return instance

    monkeypatch.setattr(llm_client, "build_llm", factory)

    response = await client.post(
        "/api/chat", json={"creator_id": "creator_001", "message": "Can I get the serum?"}
    )
    assert response.status_code == 402
    detail = response.json()["detail"]
    assert detail["code"] == "credits_exhausted"
    assert "credits are exhausted" in detail["message"]

    # the failure is still visible in the audit log
    events = (await client.get("/api/logs", params={"event_type": "llm_error"})).json()
    assert events["count"] >= 1
    assert events["events"][0]["status"] == "error"

