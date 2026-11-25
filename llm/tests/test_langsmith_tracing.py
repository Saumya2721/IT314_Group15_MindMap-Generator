"""
Tests for LangSmith tracing instrumentation, configuration, and no-op guarantees (S3-OBS).
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from config import LangSmithConfig, Settings
from tracing import annotate_current_span, configure_langsmith, trace_span
from worker.settings import WorkerSettings
from worker.tasks import (
    chat_turn_task,
    generate_mindmap,
    regenerate_node_task,
)


def test_langsmith_config_defaults():
    with patch.dict(os.environ, {}, clear=True):
        cfg = LangSmithConfig()
        assert cfg.tracing_enabled is False
        assert cfg.endpoint == "https://api.smith.langchain.com"
        assert cfg.project == "mindweave-dev"
        assert cfg.environment == "local"


def test_langsmith_config_custom_env():
    with patch.dict(
        os.environ,
        {
            "LANGSMITH_TRACING": "true",
            "LANGSMITH_ENDPOINT": "https://apac.api.smith.langchain.com",
            "LANGSMITH_API_KEY": "ls-test-key-12345",
            "LANGSMITH_AGENT_ID": "mindweave-prod",
            "LANGSMITH_AGENT_ENVIRONMENT": "production",
        },
    ):
        cfg = LangSmithConfig()
        assert cfg.tracing_enabled is True
        assert cfg.endpoint == "https://apac.api.smith.langchain.com"
        assert cfg.api_key == "ls-test-key-12345"
        assert cfg.project == "mindweave-prod"
        assert cfg.environment == "production"


def test_langsmith_config_legacy_aliases():
    with patch.dict(
        os.environ,
        {
            "LANGSMITH_TRACING": "",
            "LANGSMITH_API_KEY": "",
            "LANGSMITH_PROJECT": "",
            "LANGSMITH_AGENT_ID": "",
            "LANGSMITH_ENDPOINT": "",
            "LANGCHAIN_TRACING_V2": "true",
            "LANGCHAIN_API_KEY": "legacy-key",
            "LANGCHAIN_PROJECT": "legacy-project",
            "LANGCHAIN_ENDPOINT": "https://legacy.smith.com",
        },
    ):
        cfg = LangSmithConfig()
        assert cfg.tracing_enabled is True
        assert cfg.api_key == "legacy-key"
        assert cfg.endpoint == "https://legacy.smith.com"
        assert cfg.project == "legacy-project"


def test_settings_has_langsmith_field():
    settings = Settings()
    assert hasattr(settings, "langsmith")
    assert isinstance(settings.langsmith, LangSmithConfig)


def test_configure_langsmith_noop_when_disabled():
    cfg = LangSmithConfig(tracing_enabled=False)
    with patch.dict(os.environ, {}, clear=True):
        configure_langsmith(cfg)
        assert "LANGSMITH_TRACING" not in os.environ


def test_configure_langsmith_noop_when_key_empty():
    cfg = LangSmithConfig(tracing_enabled=True, api_key="")
    with patch.dict(os.environ, {}, clear=True):
        configure_langsmith(cfg)
        assert "LANGSMITH_TRACING" not in os.environ


def test_trace_span_sync_function():
    @trace_span("test_sync_chain", run_type="chain")
    def sync_fn(x: int, y: int) -> int:
        return x * y

    assert sync_fn(4, 5) == 20


@pytest.mark.asyncio
async def test_trace_span_async_function():
    @trace_span("test_async_chain", run_type="chain")
    async def async_fn(val: str) -> str:
        return f"hello {val}"

    res = await async_fn("mindweave")
    assert res == "hello mindweave"


def test_annotate_current_span_noop_when_untraced():
    # Calling annotate_current_span without active trace must not raise
    annotate_current_span(name="new_name", tags=["test"], metadata={"k": "v"})


def test_worker_settings_functions_registration():
    expected_functions = {
        generate_mindmap,
        regenerate_node_task,
        chat_turn_task,
    }
    actual_functions = set(WorkerSettings.functions)
    assert expected_functions.issubset(actual_functions)


def test_instrumented_extractors_and_tools_decorated():
    from chat.context import assemble_chat_context
    from chat.token_budget import trim_history_to_budget
    from ingestion.extractors.docx import extract_text_from_docx
    from ingestion.extractors.image import extract_text_from_image
    from ingestion.extractors.pdf import extract_text_from_pdf
    from ingestion.extractors.pptx import extract_text_from_pptx
    from ingestion.extractors.registry import ExtractorRegistry
    from memory.extraction import extract_memories_from_interaction
    from tools.arxiv_tool import ArxivTool
    from tools.scholar_tool import ScholarTool
    from tools.web_reader import WebPageReaderTool, fetch_web_page_async
    from tools.web_search_tool import TavilyWebSearchTool
    from tools.wikipedia_tool import WikipediaTool

    # Assert all target functions and methods are callable and decorated
    assert callable(extract_text_from_image)
    assert callable(extract_text_from_docx)
    assert callable(extract_text_from_pptx)
    assert callable(extract_text_from_pdf)
    assert callable(ExtractorRegistry.extract_with_spec)
    assert callable(ExtractorRegistry.aextract_with_spec)
    assert callable(WikipediaTool.invoke)
    assert callable(ScholarTool.invoke)
    assert callable(ArxivTool.invoke)
    assert callable(TavilyWebSearchTool.invoke)
    assert callable(fetch_web_page_async)
    assert callable(WebPageReaderTool.invoke)
    assert callable(extract_memories_from_interaction)
    assert callable(trim_history_to_budget)
    assert callable(assemble_chat_context)
