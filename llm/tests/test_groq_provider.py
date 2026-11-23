"""Unit tests for GroqProvider structured output and model availability (S3-L4)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

LLM_ROOT = Path(__file__).resolve().parents[1]
if str(LLM_ROOT / "mindmap_llm") not in sys.path:
    sys.path.insert(0, str(LLM_ROOT / "mindmap_llm"))

import pytest
from providers.base import Message
from providers.groq_provider import GroqProvider


def _build_mock_groq_response(text: str = "{}") -> MagicMock:
    choice = MagicMock()
    choice.message.content = text
    choice.message.tool_calls = None
    choice.finish_reason = "stop"
    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 20
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    return resp


def _create_test_provider(model: str = "openai/gpt-oss-120b") -> GroqProvider:
    provider = GroqProvider.__new__(GroqProvider)
    provider._model = model
    mock_client = MagicMock()
    mock_client.chat = MagicMock()
    mock_client.chat.completions = MagicMock()
    mock_client.models = MagicMock()
    provider._client = mock_client
    return provider


def test_groq_provider_passes_response_format() -> None:
    async def _run() -> None:
        provider = _create_test_provider("openai/gpt-oss-120b")
        mock_create = AsyncMock(return_value=_build_mock_groq_response('{"nodes":[],"edges":[]}'))
        provider._client.chat.completions.create = mock_create

        resp = await provider.generate(
            [Message(role="user", content="Generate map")],
            response_format={"type": "json_object"},
        )

        assert resp.text == '{"nodes":[],"edges":[]}'
        mock_create.assert_awaited_once()
        call_kwargs = mock_create.await_args.kwargs
        assert "response_format" in call_kwargs
        assert call_kwargs["response_format"] == {"type": "json_object"}

    asyncio.run(_run())


def test_groq_provider_omits_response_format_by_default() -> None:
    async def _run() -> None:
        provider = _create_test_provider("openai/gpt-oss-120b")
        mock_create = AsyncMock(return_value=_build_mock_groq_response("unformatted text"))
        provider._client.chat.completions.create = mock_create

        resp = await provider.generate([Message(role="user", content="Generate map")])

        assert resp.text == "unformatted text"
        mock_create.assert_awaited_once()
        call_kwargs = mock_create.await_args.kwargs
        assert "response_format" not in call_kwargs

    asyncio.run(_run())


def test_groq_provider_supports_structured_output_property() -> None:
    provider = _create_test_provider("openai/gpt-oss-120b")
    assert provider.supports_structured_output is True


def test_groq_provider_validate_model_availability_success() -> None:
    async def _run() -> None:
        provider = _create_test_provider("openai/gpt-oss-120b")
        mock_model1 = MagicMock(id="openai/gpt-oss-120b")
        mock_model2 = MagicMock(id="qwen/qwen3.8-27b")
        models_page = MagicMock(data=[mock_model1, mock_model2])
        provider._client.models.list = AsyncMock(return_value=models_page)

        available = await provider.validate_model_availability("openai/gpt-oss-120b")
        assert available is True

    asyncio.run(_run())


def test_groq_provider_validate_model_availability_missing_raises() -> None:
    async def _run() -> None:
        provider = _create_test_provider("nonexistent-model")
        mock_model1 = MagicMock(id="openai/gpt-oss-120b")
        models_page = MagicMock(data=[mock_model1])
        provider._client.models.list = AsyncMock(return_value=models_page)

        with pytest.raises(ValueError, match="is not available on Groq"):
            await provider.validate_model_availability("nonexistent-model")

    asyncio.run(_run())
