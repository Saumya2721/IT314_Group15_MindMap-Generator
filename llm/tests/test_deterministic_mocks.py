"""Unit tests for deterministic fake LLM and fake tools (S3-L5 Part A).

Verifies:
1. MockProvider returns deterministic, structurally valid JSON and supports scripted tool-calls.
2. FakeLLMProvider supports string sequences, LLMResponse objects, and call recording.
3. All five fake tools (arxiv_search, scholar, web_search, web_page_reader, wikipedia) match external schemas.
4. Fake tools provide proper attribution, DOIs, canonical URLs, and section hierarchies.
5. Zero external network or socket calls are made during fake execution.
6. Centralized fake tool registry integrates cleanly with test fixtures and policy layer.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from config import Settings
from providers.base import LLMResponse, Message, ToolCall
from providers.mock_provider import MockProvider
from providers.registry import get_llm_provider
from tools.base import ToolError, ToolResult
from tools.fake_tools import (
    FakeArxivTool,
    FakeScholarTool,
    FakeWebPageReaderTool,
    FakeWebSearchTool,
    FakeWikipediaTool,
    build_fake_tool_registry,
)
from tools.policy import wrap_tool_registry

from tests.conftest import FakeLLMProvider

# ---------------------------------------------------------------------------
# 1. MockProvider Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_provider_default_deterministic_response():
    """Default MockProvider returns structurally valid mind map JSON with nodes and edges."""
    provider = MockProvider(model="test-mock")
    assert provider.name == "mock:test-mock"

    msg = [Message(role="user", content="Generate a mind map on biology")]
    resp = await provider.generate(msg)

    assert resp.text is not None
    assert resp.stop_reason == "stop"
    assert len(provider.calls) == 1
    assert provider.calls[0] == msg

    data = json.loads(resp.text)
    assert "nodes" in data
    assert "edges" in data
    assert len(data["nodes"]) >= 1
    assert data["nodes"][0]["node_type"] == "root"
    assert "sources" in data["nodes"][0]


@pytest.mark.asyncio
async def test_mock_provider_scripted_responses_cycling():
    """MockProvider cycles deterministically through scripted string responses."""
    resp1 = json.dumps({"nodes": [{"id": "1", "label": "Step 1"}], "edges": []})
    resp2 = json.dumps({"nodes": [{"id": "2", "label": "Step 2"}], "edges": []})
    provider = MockProvider(responses=[resp1, resp2])

    res1 = await provider.generate([Message(role="user", content="Q1")])
    res2 = await provider.generate([Message(role="user", content="Q2")])
    res3 = await provider.generate([Message(role="user", content="Q3")])

    assert res1.text == resp1
    assert res2.text == resp2
    assert res3.text == resp1  # cycles back to first response
    assert len(provider.calls) == 3

    provider.reset()
    assert len(provider.calls) == 0
    res_after_reset = await provider.generate([Message(role="user", content="Q4")])
    assert res_after_reset.text == resp1


@pytest.mark.asyncio
async def test_mock_provider_tool_calls_support():
    """MockProvider returns deterministic tool calls when configured."""
    tool_call = ToolCall(name="wikipedia", arguments={"query": "Neuroscience"}, call_id="call_mock_1")
    provider = MockProvider(tool_calls=[tool_call])

    resp = await provider.generate([Message(role="user", content="Plan research")])
    assert resp.text is None
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "wikipedia"
    assert resp.tool_calls[0].arguments == {"query": "Neuroscience"}
    assert resp.stop_reason == "tool_use"


@pytest.mark.asyncio
async def test_mock_provider_scripted_tool_calls_sequence():
    """MockProvider returns a scripted sequence of tool-call turns."""
    tc1 = [ToolCall(name="arxiv_search", arguments={"query": "LLM"}, call_id="c1")]
    tc2 = [ToolCall(name="scholar", arguments={"query": "Attention"}, call_id="c2")]
    provider = MockProvider(scripted_tool_calls=[tc1, tc2])

    r1 = await provider.generate([Message(role="user", content="Turn 1")])
    r2 = await provider.generate([Message(role="user", content="Turn 2")])

    assert len(r1.tool_calls) == 1 and r1.tool_calls[0].name == "arxiv_search"
    assert len(r2.tool_calls) == 1 and r2.tool_calls[0].name == "scholar"

    # Once scripted tool calls are exhausted, falls back to default responses
    r3 = await provider.generate([Message(role="user", content="Turn 3")])
    assert r3.text is not None
    assert "nodes" in json.loads(r3.text)


def test_mock_provider_registry_resolution():
    """get_llm_provider handles both 'mock' and 'deterministic' provider identifiers."""
    settings = Settings()
    prov_mock = get_llm_provider(settings, provider="mock", model="fast-mock")
    assert isinstance(prov_mock, MockProvider)
    assert prov_mock.name == "mock:fast-mock"

    prov_det = get_llm_provider(settings, provider="deterministic", model="audit-model")
    assert isinstance(prov_det, MockProvider)
    assert prov_det.name == "mock:audit-model"


# ---------------------------------------------------------------------------
# 2. FakeLLMProvider Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_llm_provider_string_and_llmresponse():
    """FakeLLMProvider in conftest supports string payloads and LLMResponse objects."""
    custom_resp = LLMResponse(
        text=None,
        tool_calls=[ToolCall(name="web_search", arguments={"query": "AI"}, call_id="call_1")],
        stop_reason="tool_use",
    )
    provider = FakeLLMProvider(["initial-string", custom_resp])

    r1 = await provider.generate([Message(role="user", content="1")])
    assert r1.text == "initial-string"
    assert len(r1.tool_calls) == 0

    r2 = await provider.generate([Message(role="user", content="2")])
    assert r2.text is None
    assert len(r2.tool_calls) == 1
    assert r2.tool_calls[0].name == "web_search"

    # Empty fallback when queue is empty
    r3 = await provider.generate([Message(role="user", content="3")])
    assert r3.text == ""
    assert r3.stop_reason == "end_turn"


# ---------------------------------------------------------------------------
# 3. Fake Tools Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_arxiv_tool():
    """FakeArxivTool returns deterministic paper metadata with arXiv ID and valid schema."""
    tool = FakeArxivTool()
    spec = tool.spec
    assert spec.name == "arxiv_search"
    assert "query" in spec.input_schema["required"]

    results = await tool.invoke(query="deep residual networks")
    assert len(results) == 1
    r = results[0]
    assert "arxiv.org/abs/" in r.source_url
    assert "Advances in Deep Residual Networks" in (r.source_title or "")
    assert r.metadata.get("primary_category") == "cs.AI"
    assert r.metadata.get("arxiv_id") is not None
    assert len(tool.calls) == 1

    # Empty query yields empty list
    empty_res = await tool.invoke(query="")
    assert empty_res == []


@pytest.mark.asyncio
async def test_fake_scholar_tool():
    """FakeScholarTool returns deterministic scholarly results with DOIs and author metadata."""
    tool = FakeScholarTool()
    spec = tool.spec
    assert spec.name == "scholar"
    assert "query" in spec.input_schema["required"]

    results = await tool.invoke(query="attention mechanisms")
    assert len(results) == 1
    r = results[0]
    assert "semanticscholar.org/paper/" in r.source_url
    assert r.doi is not None
    assert r.doi.startswith("10.1000/mock.doi.")
    assert len(r.metadata.get("authors", [])) >= 1
    assert r.metadata.get("year") == 2023


@pytest.mark.asyncio
async def test_fake_web_search_tool():
    """FakeWebSearchTool returns deterministic web search snippets and URLs."""
    tool = FakeWebSearchTool()
    spec = tool.spec
    assert spec.name == "web_search"
    assert "query" in spec.input_schema["required"]

    results = await tool.invoke(query="photosynthesis process")
    assert len(results) == 1
    r = results[0]
    assert "https://example.com/search/" in r.source_url
    assert "Photosynthesis Process" in (r.source_title or "")
    assert "content" in r.__dict__ and len(r.content) > 10


@pytest.mark.asyncio
async def test_fake_web_page_reader_tool():
    """FakeWebPageReaderTool validates URL scheme and returns deterministic Markdown."""
    tool = FakeWebPageReaderTool()
    spec = tool.spec
    assert spec.name == "web_page_reader"
    assert "url" in spec.input_schema["required"]

    results = await tool.invoke(url="https://docs.example.org/guide")
    assert len(results) == 1
    r = results[0]
    assert r.source_url == "https://docs.example.org/guide"
    assert "# Document at docs.example.org" in r.content
    assert "## Executive Summary" in r.content

    # Requires url argument
    with pytest.raises(ToolError, match="URL argument is required"):
        await tool.invoke(url="")

    # Blocks unsupported schemes
    with pytest.raises(ToolError, match="Unsupported URL scheme"):
        await tool.invoke(url="ftp://files.example.com/data")


@pytest.mark.asyncio
async def test_fake_wikipedia_tool():
    """FakeWikipediaTool returns canonical URL, section tree, CC BY-SA 4.0 license and attribution."""
    tool = FakeWikipediaTool()
    spec = tool.spec
    assert spec.name == "wikipedia"
    assert "query" in spec.input_schema["required"]

    results = await tool.invoke(query="Artificial Intelligence")
    assert len(results) == 1
    r = results[0]
    assert r.source_title == "Artificial Intelligence"
    assert r.source_url == "https://en.wikipedia.org/wiki/Artificial_Intelligence"
    assert r.license == "CC BY-SA 4.0"
    assert "CC BY-SA 4.0" in (r.attribution or "")
    assert r.source_url in (r.attribution or "")
    assert r.sections is not None
    assert "Core Principles" in r.sections


@pytest.mark.asyncio
async def test_fake_tool_custom_results_injection():
    """All fake tools allow injecting custom fixture results."""
    custom = [ToolResult(content="Injected fact", source_title="Custom Source", source_url="https://custom.org/1")]
    wiki = FakeWikipediaTool(custom_results=custom)
    res = await wiki.invoke(query="Anything")
    assert res == custom


# ---------------------------------------------------------------------------
# 4. Zero Network Calls Guarantee
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_network_calls_during_fake_tool_execution():
    """Guarantees fake tools make zero network requests or socket calls."""
    tools = build_fake_tool_registry()

    with patch("httpx.AsyncClient.get", side_effect=AssertionError("Unexpected HTTP GET call!")):
        with patch("httpx.AsyncClient.post", side_effect=AssertionError("Unexpected HTTP POST call!")):
            with patch("socket.getaddrinfo", side_effect=AssertionError("Unexpected DNS resolution call!")):
                r1 = await tools["arxiv_search"].invoke(query="AI")
                r2 = await tools["scholar"].invoke(query="AI")
                r3 = await tools["web_search"].invoke(query="AI")
                r4 = await tools["web_page_reader"].invoke(url="https://example.com")
                r5 = await tools["wikipedia"].invoke(query="AI")

                assert len(r1) == 1
                assert len(r2) == 1
                assert len(r3) == 1
                assert len(r4) == 1
                assert len(r5) == 1


# ---------------------------------------------------------------------------
# 5. Registry Integration & Policy Wrapping
# ---------------------------------------------------------------------------


def test_build_fake_tool_registry_structure():
    """build_fake_tool_registry exposes all 5 required tools."""
    reg = build_fake_tool_registry()
    expected_keys = {"arxiv_search", "scholar", "web_search", "web_page_reader", "wikipedia"}
    assert set(reg.keys()) == expected_keys
    for name, tool in reg.items():
        assert tool.spec.name == name


@pytest.mark.asyncio
async def test_fake_tool_registry_with_policy_layer():
    """Fake tool registry integrates cleanly with the policy enforcement wrapper."""
    settings = Settings()
    reg = build_fake_tool_registry()
    wrapped = wrap_tool_registry(reg, settings)

    assert set(wrapped.keys()) == {"arxiv_search", "scholar", "web_search", "web_page_reader", "wikipedia"}
    res = await wrapped["wikipedia"].invoke(query="Cognitive Science")
    assert len(res) == 1
    assert res[0].source_title == "Cognitive Science"


@pytest.mark.asyncio
async def test_conftest_fixtures(fake_tools, mock_llm_provider):
    """Test conftest fixtures 'fake_tools' and 'mock_llm_provider'."""
    assert len(fake_tools) == 5
    res = await fake_tools["arxiv_search"].invoke(query="Test")
    assert len(res) == 1

    llm_resp = await mock_llm_provider.generate([Message(role="user", content="Hi")])
    assert llm_resp.text is not None
    assert "nodes" in json.loads(llm_resp.text)


# ---------------------------------------------------------------------------
# 6. Regression Tests for Part A Code Review Corrections
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_provider_empty_responses_fallback():
    """Empty response list uses default response without crashing or zero division."""
    provider = MockProvider(responses=[])
    resp = await provider.generate([Message(role="user", content="Hello")])
    assert resp.text is not None
    data = json.loads(resp.text)
    assert "nodes" in data
    assert len(data["nodes"]) >= 1


@pytest.mark.asyncio
async def test_mock_provider_reset_replays_scripted_tool_calls():
    """Reset restores scripted tool calls sequence and resets message history and index."""
    tc = [ToolCall(name="scholar", arguments={"query": "Graphs"}, call_id="c_graph")]
    provider = MockProvider(scripted_tool_calls=[tc])

    # Turn 1: consumes scripted tool call
    r1 = await provider.generate([Message(role="user", content="1")])
    assert len(r1.tool_calls) == 1
    assert r1.tool_calls[0].name == "scholar"
    assert len(provider.calls) == 1

    # Turn 2: falls back to default response
    r2 = await provider.generate([Message(role="user", content="2")])
    assert r2.text is not None
    assert len(provider.calls) == 2

    # Reset: restores initial scripted tool calls
    provider.reset()
    assert len(provider.calls) == 0

    # Turn 3: replays scripted tool call from beginning
    r3 = await provider.generate([Message(role="user", content="3")])
    assert len(r3.tool_calls) == 1
    assert r3.tool_calls[0].name == "scholar"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_fake_wikipedia_tool_whitespace_query_fallback():
    """Whitespace-only query correctly falls back to title parameter in kwargs."""
    tool = FakeWikipediaTool()
    results = await tool.invoke(query="   ", title="Cellular Respiration")
    assert len(results) == 1
    assert results[0].source_title == "Cellular Respiration"
    assert "Cellular_Respiration" in results[0].source_url


@pytest.mark.asyncio
async def test_fake_wikipedia_tool_sections_metadata_mutation_isolation():
    """Mutating result.sections does not mutate result.metadata['sections']."""
    tool = FakeWikipediaTool()
    results = await tool.invoke(query="Genetics")
    assert len(results) == 1
    res = results[0]

    original_metadata_sections = list(res.metadata["sections"])
    assert res.sections is not None
    res.sections.append("Mutated Section Test")

    assert "Mutated Section Test" not in res.metadata["sections"]
    assert res.metadata["sections"] == original_metadata_sections
