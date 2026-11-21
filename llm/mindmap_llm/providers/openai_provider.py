"""OpenAI-compatible LLMProvider implementation using httpx.AsyncClient (S3-L2)."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from providers.base import LLMProvider, LLMResponse, Message, ToolCall, ToolSpec
from tracing import trace_span

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    """OpenAI / OpenAI-compatible vendor adapter (e.g. OpenAI, vLLM, OpenRouter)."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_s: float = 90.0,
        api_base: str | None = None,
        default_temperature: float = 0.2,
        default_max_tokens: int = 4096,
    ):
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s
        self._api_base = (api_base or "https://api.openai.com/v1").rstrip("/")
        self._default_temperature = default_temperature
        self._default_max_tokens = default_max_tokens

    @property
    def name(self) -> str:
        return f"openai:{self._model}"

    def _to_openai_messages(self, messages: list[Message], system: str | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if system:
            out.append({"role": "system", "content": system})
        for m in messages:
            if m.role == "tool":
                out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    def _to_openai_tools(self, tools: list[ToolSpec] | None) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]

    @trace_span("openai_generate", run_type="llm")
    async def generate(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        effective_max_tokens = max_tokens or self._default_max_tokens
        effective_temp = temperature if temperature is not None else self._default_temperature

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": self._to_openai_messages(messages, system=system),
            "max_tokens": effective_max_tokens,
            "temperature": effective_temp,
        }
        openai_tools = self._to_openai_tools(tools)
        if openai_tools:
            payload["tools"] = openai_tools

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            resp = await client.post(f"{self._api_base}/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        msg = choice.get("message", {})
        text = msg.get("content")

        tool_calls: list[ToolCall] = []
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                func = tc.get("function", {})
                args = func.get("arguments", "{}")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                tool_calls.append(ToolCall(name=func.get("name", ""), arguments=args, call_id=tc.get("id", "")))

        usage = data.get("usage", {})
        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=choice.get("finish_reason"),
            usage_input_tokens=usage.get("prompt_tokens"),
            usage_output_tokens=usage.get("completion_tokens"),
            raw=data,
        )
