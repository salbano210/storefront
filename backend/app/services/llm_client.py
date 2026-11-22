"""OpenRouter client.

Thin async wrapper over the OpenAI-compatible OpenRouter API:
  * every call is routed through OpenRouter;
  * structured output is requested as JSON and validated by the caller;
  * one fallback model is attempted on transport/5xx errors;
  * each call is logged as an ``llm_response`` event with tokens, cost and
    latency, so the logs page can show real model behaviour.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from .. import settings
from .. import guardrails
from . import event_logger as events
from .event_logger import EventLogger

MAX_TEXT_PREVIEW = 4000


class LLMError(Exception):
    """Raised when a model call cannot be completed."""


class LLMCreditsExhausted(LLMError):
    """The OpenRouter key in use has no credits left (HTTP 402)."""


def _is_credit_error(exc: Exception) -> bool:
    """Detect OpenRouter's out-of-credits responses.

    A capped demo key will hit this eventually, and the difference between
    "your credits ran out" and "the provider is unreachable" matters to whoever
    is standing in front of an audience.
    """
    if getattr(exc, "status_code", None) == 402:
        return True
    text = str(exc).lower()
    return (
        "insufficient credit" in text
        or "credit limit" in text
        or "requires more credits" in text
    )


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class LLMResult:
    text: str = ""
    parsed: dict[str, Any] | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    requested_model: str = ""
    used_fallback: bool = False
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    latency_ms: int = 0


def _extract_cost(response: Any) -> float | None:
    try:
        raw = response.model_dump()
    except Exception:  # pragma: no cover - defensive
        return None
    usage = raw.get("usage") or {}
    if not isinstance(usage, dict):
        return None
    for key in ("cost", "total_cost"):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def build_llm(
    api_key: str, *, logger: EventLogger | None = None, fallback_model: str | None = None
) -> LLMClient:
    """Factory so tests can substitute a deterministic fake."""
    return LLMClient(api_key, logger=logger, fallback_model=fallback_model)


class LLMClient:
    def __init__(
        self,
        api_key: str,
        *,
        logger: EventLogger | None = None,
        fallback_model: str | None = None,
    ) -> None:
        if not api_key:
            raise LLMError("No OpenRouter API key available.")
        self.logger = logger
        self.fallback_model = fallback_model or settings.DEFAULT_MODELS["fallback"]
        headers = {
            "HTTP-Referer": settings.OPENROUTER_APP_URL,
            "X-Title": settings.OPENROUTER_APP_NAME,
        }
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=settings.OPENROUTER_BASE_URL,
            timeout=settings.OPENROUTER_TIMEOUT_SECONDS,
            default_headers=headers,
            max_retries=0,
        )

    async def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        agent: str | None = None,
        schema: dict[str, Any] | None = None,
        schema_name: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        temperature: float = 0.0,
        max_tokens: int | None = 900,
    ) -> LLMResult:
        system_prompt = system
        if schema is not None:
            system_prompt += (
                "\n\nRespond ONLY with a single JSON object matching this schema:\n"
                + json.dumps(schema, indent=2)
            )

        attempts = [model]
        if self.fallback_model and self.fallback_model != model:
            attempts.append(self.fallback_model)

        last_error = "unknown error"
        credit_error = False
        for index, candidate in enumerate(attempts):
            try:
                return await self._call(
                    model=candidate,
                    requested_model=model,
                    system=system_prompt,
                    messages=messages,
                    schema=schema,
                    schema_name=schema_name,
                    tools=tools,
                    tool_choice=tool_choice,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    agent=agent,
                    used_fallback=index > 0,
                )
            except Exception as exc:  # noqa: BLE001 - normalised into LLMError
                last_error = f"{type(exc).__name__}: {exc}"
                if _is_credit_error(exc):
                    credit_error = True
                if self.logger is not None:
                    await self.logger.log(
                        events.LLM_ERROR,
                        agent=agent,
                        status="error",
                        summary=f"{candidate} failed: {last_error}",
                        payload={"model": candidate, "error": last_error},
                    )
                if index < len(attempts) - 1:
                    if self.logger is not None:
                        await self.logger.log(
                            events.LLM_FALLBACK,
                            agent=agent,
                            summary=f"Falling back to {attempts[index + 1]}",
                            payload={
                                "from": candidate,
                                "to": attempts[index + 1],
                                "error": last_error,
                            },
                        )
                    continue
                if credit_error:
                    raise LLMCreditsExhausted(
                        "the OpenRouter key in use has no credits remaining"
                    ) from exc
                raise LLMError(f"OpenRouter call failed: {last_error}") from exc
        if credit_error:  # pragma: no cover - defensive
            raise LLMCreditsExhausted("the OpenRouter key in use has no credits remaining")
        raise LLMError(f"OpenRouter call failed: {last_error}")  # pragma: no cover


    async def _call(
        self,
        *,
        model: str,
        requested_model: str,
        system: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any] | None,
        schema_name: str | None,
        tools: list[dict[str, Any]] | None,
        tool_choice: str,
        temperature: float,
        max_tokens: int | None,
        agent: str | None,
        used_fallback: bool,
    ) -> LLMResult:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, *messages],
            "temperature": temperature,
        }
        if schema is not None:
            payload["response_format"] = {"type": "json_object"}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        if max_tokens:
            payload["max_tokens"] = max_tokens

        started = time.perf_counter()
        response = await self._client.chat.completions.create(**payload)
        latency_ms = int((time.perf_counter() - started) * 1000)

        message = response.choices[0].message
        text = message.content or ""
        tool_calls = [
            ToolCall(
                id=call.id or f"call_{index}",
                name=call.function.name,
                arguments=call.function.arguments or "{}",
            )
            for index, call in enumerate(getattr(message, "tool_calls", None) or [])
        ]

        usage = getattr(response, "usage", None)
        result = LLMResult(
            text=text,
            parsed=self._parse_json(text) if schema is not None else None,
            tool_calls=tool_calls,
            model=model,
            requested_model=requested_model,
            used_fallback=used_fallback,
            tokens_in=getattr(usage, "prompt_tokens", None) if usage else None,
            tokens_out=getattr(usage, "completion_tokens", None) if usage else None,
            cost_usd=_extract_cost(response),
            latency_ms=latency_ms,
        )

        if self.logger is not None:
            # Never persist a leaked system prompt: a creator can read their own
            # session's logs, so echoing internal instructions back would hand
            # them the configuration via the audit trail.
            logged_text = text[:MAX_TEXT_PREVIEW]
            if guardrails.find_prompt_leak(text):
                logged_text = "[withheld: the model echoed its internal instructions]"
            await self.logger.log(
                events.LLM_RESPONSE,
                agent=agent,
                summary=self._summary(result, schema_name=schema_name),
                payload={
                    "model": result.model,
                    "requested_model": result.requested_model,
                    "used_fallback": result.used_fallback,
                    "temperature": temperature,
                    "system_prompt_chars": len(system),
                    "messages": messages,
                    "tools_available": [t["function"]["name"] for t in (tools or [])],
                    "tool_calls": [
                        {"name": call.name, "arguments": self._safe_json(call.arguments)}
                        for call in tool_calls
                    ],
                    "text": logged_text,
                    "parsed": result.parsed,
                },
                model=result.model,
                latency_ms=result.latency_ms,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                cost_usd=result.cost_usd,
            )
        return result

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any] | None:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:]
        try:
            value = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _safe_json(raw: str) -> Any:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return raw

    @staticmethod
    def _summary(result: LLMResult, *, schema_name: str | None = None) -> str:
        parts = [result.model]
        if result.used_fallback:
            parts.append(f"(fallback from {result.requested_model})")
        if schema_name:
            parts.append(f"schema={schema_name}")
        if result.tool_calls:
            parts.append("tools=" + ",".join(call.name for call in result.tool_calls))
        parts.append(f"{result.latency_ms}ms")
        if result.tokens_in is not None:
            parts.append(f"{result.tokens_in}in/{result.tokens_out}out")
        return " ".join(parts)

