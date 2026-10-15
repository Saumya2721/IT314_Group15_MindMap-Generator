"""
LLMProvider implementation using the Groq SDK.

Groq provides ultra-fast inference for open-source models (LLaMA, Mixtral,
Gemma, etc.) via an OpenAI-compatible API. This adapter maps the generic
LLMProvider interface to the Groq Python SDK's chat completions endpoint.

Swap in or out by setting LLM_PROVIDER=groq in .env — orchestration code
never needs to change.
"""

from __future__ import annotations

from typing import Any

from providers.base import LLMProvider, LLMResponse, Message, ToolCall, ToolSpec


class GroqProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_s: float = 90.0,
        api_base: str | None = None,
    ):
        try:
            from groq import (
                AsyncGroq,
            )  # local import: don't require the SDK unless this provider is selected
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "groq package not installed. `pip install groq` "
                "or select a different LLM_PROVIDER."
            ) from exc

        client_kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout_s}
        if api_base:
            client_kwargs["base_url"] = api_base
        self._client = AsyncGroq(**client_kwargs)
        self._model = model

    @property
    def name(self) -> str:
        return f"groq:{self._model}"

    def _to_groq_messages(
        self, messages: list[Message], system: str | None = None
    ) -> list[dict[str, Any]]:
        """Convert vendor-neutral Messages to Groq/OpenAI chat format."""
        out: list[dict[str, Any]] = []
        if system:
            out.append({"role": "system", "content": system})
        for m in messages:
            if m.role == "tool":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": m.tool_call_id,
                        "content": m.content,
                    }
                )
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    def _to_groq_tools(
        self, tools: list[ToolSpec] | None
    ) -> list[dict[str, Any]] | None:
        """Convert vendor-neutral ToolSpecs to Groq/OpenAI function-calling format."""
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

    async def generate(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        import json as _json

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens or 4096,
            "temperature": temperature,
            "messages": self._to_groq_messages(messages, system=system),
        }
        groq_tools = self._to_groq_tools(tools)
        if groq_tools:
            kwargs["tools"] = groq_tools

        resp = await self._client.chat.completions.create(**kwargs)

        choice = resp.choices[0]
        text = choice.message.content

        tool_calls: list[ToolCall] = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                arguments = tc.function.arguments
                # Groq returns arguments as a JSON string; parse it.
                if isinstance(arguments, str):
                    arguments = _json.loads(arguments)
                tool_calls.append(
                    ToolCall(
                        name=tc.function.name,
                        arguments=arguments,
                        call_id=tc.id,
                    )
                )

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=choice.finish_reason,
            usage_input_tokens=getattr(resp.usage, "prompt_tokens", None),
            usage_output_tokens=getattr(resp.usage, "completion_tokens", None),
            raw=resp,
        )
