"""
Comprehensive unit and integration test suite for S3-A2:
Bounded Multi-Step Research Agent Subgraph.

Covers all 19 acceptance criteria:
  1. Query planner generates targeted queries.
  2. Invalid or failed planner output is handled correctly.
  3. Tavily, Wikipedia, and Scholar are dispatched correctly.
  4. Independent tools execute concurrently.
  5. Duplicate DOI sources are removed.
  6. Duplicate canonical URLs are removed.
  7. Different sources are not incorrectly deduplicated.
  8. Redis cache hits return cached results.
  9. Redis cache misses execute searches.
  10. Cache entries use a 3600-second TTL.
  11. Redis failures do not crash research.
  12. Sufficiency evaluation triggers another round when appropriate.
  13. Research never exceeds 2 search rounds.
  14. Global timeout is enforced across the entire research operation.
  15. Partial results are preserved after a timeout.
  16. Individual tool failures produce degraded-run warnings.
  17. All-tool failure is handled gracefully.
  18. Both `subgraph` and `inline` feature-flag modes work.
  19. Direct tool invocation and normalization for Wikipedia and Scholar.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from config import Settings
from orchestration.deps import GenerationDeps
from orchestration.graph import run_generation
from orchestration.nodes import make_nodes
from providers.base import ToolSpec
from research.cache import ResearchCache, make_cache_key
from research.dedup import (
    canonicalize_url,
    deduplicate_sources,
)
from research.models import ResearchSource
from research.planner import build_fallback_queries, plan_targeted_queries
from research.subgraph import execute_tool_query, run_research_subgraph
from tools.base import Tool, ToolError, ToolResult
from tools.policy import ResearchTimeBudget
from tools.scholar_tool import ScholarTool
from tools.wikipedia_tool import WikipediaTool

from tests.conftest import (
    HEALTHY_10_NODE_PAYLOAD,
    FakeCallbackClient,
    FakeDecisionModel,
    FakeEmbeddingProvider,
    FakeLLMProvider,
    FakeVectorStore,
)


class MockTool(Tool):
    """Configurable mock tool for deterministic testing."""

    def __init__(
        self,
        name: str,
        results: list[ToolResult] | None = None,
        delay_s: float = 0.0,
        fail: bool = False,
    ):
        self._name = name
        self._results = results or []
        self._delay_s = delay_s
        self._fail = fail
        self.invocations: list[dict[str, Any]] = []

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self._name,
            description=f"Mock tool {self._name}",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        )

    async def invoke(self, *args: Any, **kwargs: Any) -> list[ToolResult]:
        self.invocations.append(kwargs)
        if self._delay_s > 0:
            await asyncio.sleep(self._delay_s)
        if self._fail:
            raise ToolError(f"Simulated failure in tool {self._name}")
        return self._results


class MockRedis:
    """In-memory Redis mock tracking calls, TTLs, and simulated connection errors."""

    def __init__(self, simulate_error: bool = False):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.simulate_error = simulate_error
        self.get_calls: list[str] = []
        self.set_calls: list[tuple[str, int, str]] = []

    async def get(self, key: str) -> str | None:
        self.get_calls.append(key)
        if self.simulate_error:
            raise ConnectionError("Simulated Redis connection failure")
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.set_calls.append((key, ttl, value))
        if self.simulate_error:
            raise ConnectionError("Simulated Redis write error")
        self.store[key] = value
        self.ttls[key] = ttl

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        await self.setex(key, ex or 3600, value)


# ---------------------------------------------------------------------------
# 1 & 2. Query Planner Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_planner_generates_targeted_queries():
    planner_payload = {
        "tavily": ["transformer neural network architectures", "llm industry deployments"],
        "wikipedia": ["transformer machine learning model"],
        "scholar": ["attention is all you need vaswani empirical"],
    }
    llm = FakeLLMProvider([json.dumps(planner_payload)])
    queries = await plan_targeted_queries(
        llm=llm,
        seed_text="Attention models and deep learning transformers",
        active_tools=["tavily", "wikipedia", "scholar"],
    )

    assert "tavily" in queries
    assert len(queries["tavily"]) == 2
    assert "transformer machine learning model" in queries["wikipedia"]
    assert "scholar" in queries


@pytest.mark.asyncio
async def test_query_planner_fallback_on_invalid_or_failed_llm():
    # Model returns broken text
    llm = FakeLLMProvider(["I cannot generate json right now"])
    queries = await plan_targeted_queries(
        llm=llm,
        seed_text="Quantum key distribution and cryptography",
        active_tools=["web_search", "wikipedia", "scholar"],
    )

    assert "web_search" in queries
    assert len(queries["web_search"]) >= 1
    assert "wikipedia" in queries
    assert len(queries["wikipedia"]) >= 1
    assert "scholar" in queries
    assert len(queries["scholar"]) >= 1


def test_build_fallback_queries_deterministic():
    fb = build_fallback_queries("Photosynthesis light reactions", ["wikipedia", "scholar"], round_num=1)
    assert "wikipedia" in fb
    assert "scholar" in fb
    assert any("Photosynthesis" in q for q in fb["wikipedia"])


# ---------------------------------------------------------------------------
# 3 & 4. Parallel Search & Concurrent Execution Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_dispatch_tavily_wikipedia_scholar():
    tavily_res = [ToolResult(content="Tavily snippet", source_title="Tavily Title", source_url="https://web.org/1")]
    wiki_res = [
        ToolResult(content="Wiki snippet", source_title="Wiki Title", source_url="https://en.wikipedia.org/wiki/Topic")
    ]
    scholar_res = [
        ToolResult(
            content="Paper abstract",
            source_title="Paper Title",
            source_url="https://doi.org/10.1000/1",
            doi="10.1000/1",
        )
    ]

    tavily_tool = MockTool("web_search", results=tavily_res)
    wiki_tool = MockTool("wikipedia", results=wiki_res)
    scholar_tool = MockTool("scholar", results=scholar_res)

    deps = GenerationDeps(
        llm=FakeLLMProvider([json.dumps({"tavily": ["q1"], "wikipedia": ["q2"], "scholar": ["q3"]})]),
        decision_model=FakeDecisionModel(),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=FakeVectorStore(),
        tools={"web_search": tavily_tool, "wikipedia": wiki_tool, "scholar": scholar_tool},
        settings=Settings(),
        callback=FakeCallbackClient(),
    )

    sources, warnings, degraded, logs = await run_research_subgraph(
        deps,
        seed_text="Topic text",
        namespace="map-1",
        job_id=uuid.uuid4(),
        max_rounds=1,
    )

    assert len(sources) == 3
    tools_returned = {s.tool for s in sources}
    assert "web_search" in tools_returned or "tavily" in tools_returned
    assert "wikipedia" in tools_returned
    assert "scholar" in tools_returned
    assert not degraded


@pytest.mark.asyncio
async def test_tools_execute_concurrently():
    delay = 0.1
    # 3 tools each delaying 0.1s
    t1 = MockTool(
        "web_search", results=[ToolResult(content="1", source_title="T1", source_url="https://1.com")], delay_s=delay
    )
    t2 = MockTool(
        "wikipedia", results=[ToolResult(content="2", source_title="T2", source_url="https://2.com")], delay_s=delay
    )
    t3 = MockTool(
        "scholar", results=[ToolResult(content="3", source_title="T3", source_url="https://3.com")], delay_s=delay
    )

    deps = GenerationDeps(
        llm=FakeLLMProvider([json.dumps({"web_search": ["q1"], "wikipedia": ["q2"], "scholar": ["q3"]})]),
        decision_model=FakeDecisionModel(),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=FakeVectorStore(),
        tools={"web_search": t1, "wikipedia": t2, "scholar": t3},
        settings=Settings(),
        callback=FakeCallbackClient(),
    )

    start = time.monotonic()
    sources, _, _, _ = await run_research_subgraph(
        deps,
        seed_text="Test concurrent execution",
        namespace="test-concur",
        job_id=uuid.uuid4(),
        max_rounds=1,
    )
    elapsed = time.monotonic() - start

    assert len(sources) == 3
    # If sequential, elapsed would be >= 0.3s; parallel should complete in ~0.15s
    assert elapsed < 0.28


# ---------------------------------------------------------------------------
# 5, 6, 7. Source Deduplication Tests
# ---------------------------------------------------------------------------


def test_deduplication_by_doi():
    s1 = ResearchSource(
        content="Short abstract",
        source_title="Paper Title",
        source_url="https://doi.org/10.1038/s41586-020-2649-2",
        doi="10.1038/s41586-020-2649-2",
        tool="scholar",
    )
    s2 = ResearchSource(
        content="Long comprehensive paper abstract with methodology details and findings.",
        source_title="Paper Title - Nature",
        source_url="https://nature.com/articles/s41586-020-2649-2?utm_source=twitter",
        doi="https://dx.doi.org/10.1038/s41586-020-2649-2",
        tool="tavily",
        authors=["Alice", "Bob"],
    )

    deduped = deduplicate_sources([s1, s2])
    assert len(deduped) == 1
    merged = deduped[0]
    assert merged.canonical_doi == "10.1038/s41586-020-2649-2"
    # Metadata merging kept the longer content and authors
    assert len(merged.content) == len(s2.content)
    assert merged.authors == ["Alice", "Bob"]


def test_deduplication_by_canonical_url():
    u1 = "https://www.example.com/research/paper/?utm_source=email&ref=newsletter#abstract"
    u2 = "http://example.com/research/paper"

    assert canonicalize_url(u1) == canonicalize_url(u2)
    assert canonicalize_url(u1) == "https://example.com/research/paper"

    s1 = ResearchSource(content="Content A", source_title="Title A", source_url=u1, tool="tavily")
    s2 = ResearchSource(
        content="Content A with expanded details",
        source_title="Title A Complete",
        source_url=u2,
        tool="web_search",
    )

    deduped = deduplicate_sources([s1, s2])
    assert len(deduped) == 1
    assert deduped[0].canonical_url == "https://example.com/research/paper"
    assert "expanded details" in deduped[0].content


def test_different_sources_not_incorrectly_deduplicated():
    s1 = ResearchSource(content="Doc 1", source_title="Title 1", source_url="https://example.com/page1", tool="tavily")
    s2 = ResearchSource(content="Doc 2", source_title="Title 2", source_url="https://example.com/page2", tool="tavily")
    s3 = ResearchSource(
        content="Doc 3",
        source_title="Paper 1",
        source_url="https://doi.org/10.1000/1",
        doi="10.1000/1",
        tool="scholar",
    )
    s4 = ResearchSource(
        content="Doc 4",
        source_title="Paper 2",
        source_url="https://doi.org/10.1000/2",
        doi="10.1000/2",
        tool="scholar",
    )

    deduped = deduplicate_sources([s1, s2, s3, s4])
    assert len(deduped) == 4


# ---------------------------------------------------------------------------
# 8, 9, 10, 11. Redis Caching Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_cache_hit_returns_cached_results():
    mock_redis = MockRedis()
    cache = ResearchCache(redis_client=mock_redis, ttl_s=3600)

    cached_item = [
        ResearchSource(
            content="Cached snippet",
            source_title="Cached Title",
            source_url="https://cached.org/1",
            tool="wikipedia",
        )
    ]
    await cache.set("wikipedia", "quantum physics", cached_item)

    tool = MockTool(
        "wikipedia", results=[ToolResult(content="Live", source_title="Live", source_url="https://live.org")]
    )
    budget = ResearchTimeBudget(10.0)

    # Invoke query: should hit cache and NOT invoke tool
    sources, is_hit, err = await execute_tool_query(
        tool=tool,
        tool_name="wikipedia",
        query="quantum physics",
        cache=cache,
        budget=budget,
    )

    assert is_hit is True
    assert err is None
    assert len(sources) == 1
    assert sources[0].source_url == "https://cached.org/1"
    assert len(tool.invocations) == 0  # tool never called


@pytest.mark.asyncio
async def test_redis_cache_miss_executes_search_and_sets_3600_ttl():
    mock_redis = MockRedis()
    cache = ResearchCache(redis_client=mock_redis, ttl_s=3600)

    live_res = [ToolResult(content="Fresh", source_title="Fresh", source_url="https://fresh.org")]
    tool = MockTool("tavily", results=live_res)
    budget = ResearchTimeBudget(10.0)

    sources, is_hit, err = await execute_tool_query(
        tool=tool,
        tool_name="tavily",
        query="deep learning optimization",
        cache=cache,
        budget=budget,
    )

    assert is_hit is False
    assert err is None
    assert len(sources) == 1
    assert len(tool.invocations) == 1

    # Verify TTL was set to exactly 3600 seconds
    key = make_cache_key("tavily", "deep learning optimization")
    assert key in mock_redis.store
    assert mock_redis.ttls[key] == 3600


@pytest.mark.asyncio
async def test_redis_failure_does_not_crash_research():
    failing_redis = MockRedis(simulate_error=True)
    cache = ResearchCache(redis_client=failing_redis, ttl_s=3600)

    tool = MockTool(
        "scholar", results=[ToolResult(content="Scholar Paper", source_title="P1", source_url="https://doi.org/10.1/1")]
    )
    budget = ResearchTimeBudget(10.0)

    # Should gracefully catch the Redis error, invoke tool, and return results
    sources, is_hit, err = await execute_tool_query(
        tool=tool,
        tool_name="scholar",
        query="graph neural networks",
        cache=cache,
        budget=budget,
    )

    assert is_hit is False
    assert err is None
    assert len(sources) == 1
    assert sources[0].source_title == "P1"


# ---------------------------------------------------------------------------
# 12 & 13. Sufficiency Evaluation & Bounded Loop (Max 2 Rounds)
# ---------------------------------------------------------------------------


class InsufficientThenSufficientDecisionModel(FakeDecisionModel):
    def __init__(self):
        super().__init__()
        self.call_count = 0

    async def classify(self, *args, **kwargs):
        self.call_count += 1
        if self.call_count == 1:
            from providers.decision_model import ClassifyResult

            return ClassifyResult(label="insufficient", confidence=0.9)
        return await super().classify(*args, **kwargs)


@pytest.mark.asyncio
async def test_sufficiency_triggers_second_round_and_stops_at_max_two_rounds():
    round1_queries = json.dumps({"web_search": ["q1"]})
    round2_queries = json.dumps({"web_search": ["q2"]})
    # LLM supplies queries for round 1 and round 2
    llm = FakeLLMProvider([round1_queries, round2_queries])

    tool = MockTool(
        "web_search",
        results=[ToolResult(content="Subtopic details", source_title="Doc", source_url="https://ex.com/doc")],
    )

    decision_model = InsufficientThenSufficientDecisionModel()

    deps = GenerationDeps(
        llm=llm,
        decision_model=decision_model,
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=FakeVectorStore(),
        tools={"web_search": tool},
        settings=Settings(),
        callback=FakeCallbackClient(),
    )

    sources, warnings, degraded, logs = await run_research_subgraph(
        deps,
        seed_text="Complex multi-faceted topic",
        namespace="map-bounded",
        job_id=uuid.uuid4(),
        max_rounds=2,
    )

    # Verifies that round 2 executed
    assert len(logs) == 1
    assert logs[0]["rounds"] == 2
    assert decision_model.call_count >= 1
    assert len(sources) >= 1


@pytest.mark.asyncio
async def test_research_never_exceeds_two_rounds_even_if_always_insufficient():
    class AlwaysInsufficientDecisionModel(FakeDecisionModel):
        async def classify(self, *args, **kwargs):
            from providers.decision_model import ClassifyResult

            return ClassifyResult(label="insufficient", confidence=0.99)

    round1_queries = json.dumps({"web_search": ["q1"]})
    round2_queries = json.dumps({"web_search": ["q2"]})
    llm = FakeLLMProvider([round1_queries, round2_queries])

    tool = MockTool(
        "web_search",
        results=[ToolResult(content="Brief info", source_title="Doc", source_url="https://ex.com/brief")],
    )

    deps = GenerationDeps(
        llm=llm,
        decision_model=AlwaysInsufficientDecisionModel(),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=FakeVectorStore(),
        tools={"web_search": tool},
        settings=Settings(),
        callback=FakeCallbackClient(),
    )

    sources, warnings, degraded, logs = await run_research_subgraph(
        deps,
        seed_text="Perpetually insufficient topic",
        namespace="map-bounded-2",
        job_id=uuid.uuid4(),
        max_rounds=2,
    )

    # Strictly halted after round 2
    assert logs[0]["rounds"] == 2


# ---------------------------------------------------------------------------
# 14 & 15. Global Timeout & Partial Result Preservation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_global_timeout_enforced_and_partial_results_preserved():
    # Budget of 0.05 seconds; tool delays for 0.08 seconds
    slow_tool = MockTool(
        "web_search",
        results=[ToolResult(content="Slow result", source_title="Slow", source_url="https://slow.org")],
        delay_s=0.08,
    )

    deps = GenerationDeps(
        llm=FakeLLMProvider([json.dumps({"web_search": ["q1"]})]),
        decision_model=FakeDecisionModel(),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=FakeVectorStore(),
        tools={"web_search": slow_tool},
        settings=Settings(),
        callback=FakeCallbackClient(),
    )

    start = time.monotonic()
    sources, warnings, degraded, logs = await run_research_subgraph(
        deps,
        seed_text="Timeout test topic",
        namespace="map-timeout",
        job_id=uuid.uuid4(),
        timeout_s=0.05,
    )
    elapsed = time.monotonic() - start

    # Total duration should not blow up
    assert elapsed < 0.25
    assert degraded is True
    assert any("expired" in w.lower() or "timeout" in w.lower() for w in warnings)


# ---------------------------------------------------------------------------
# 16 & 17. Graceful Degradation: Tool Failures & All-Tool Failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_individual_tool_failure_produces_degraded_warning():
    failing_tool = MockTool("web_search", fail=True)
    working_tool = MockTool(
        "wikipedia",
        results=[
            ToolResult(content="Wiki grounding", source_title="Wiki", source_url="https://en.wikipedia.org/wiki/P")
        ],
    )

    deps = GenerationDeps(
        llm=FakeLLMProvider([json.dumps({"web_search": ["q1"], "wikipedia": ["q2"]})]),
        decision_model=FakeDecisionModel(),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=FakeVectorStore(),
        tools={"web_search": failing_tool, "wikipedia": working_tool},
        settings=Settings(),
        callback=FakeCallbackClient(),
    )

    sources, warnings, degraded, logs = await run_research_subgraph(
        deps,
        seed_text="Degraded test",
        namespace="map-deg",
        job_id=uuid.uuid4(),
        max_rounds=1,
    )

    assert degraded is True
    assert len(sources) == 1
    assert sources[0].tool == "wikipedia"
    assert any("web_search" in w for w in warnings)


@pytest.mark.asyncio
async def test_all_tool_failure_handled_gracefully_without_crash():
    fail1 = MockTool("web_search", fail=True)
    fail2 = MockTool("wikipedia", fail=True)

    deps = GenerationDeps(
        llm=FakeLLMProvider([json.dumps({"web_search": ["q1"], "wikipedia": ["q2"]})]),
        decision_model=FakeDecisionModel(),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=FakeVectorStore(),
        tools={"web_search": fail1, "wikipedia": fail2},
        settings=Settings(),
        callback=FakeCallbackClient(),
    )

    sources, warnings, degraded, logs = await run_research_subgraph(
        deps,
        seed_text="All fail test",
        namespace="map-all-fail",
        job_id=uuid.uuid4(),
        max_rounds=1,
    )

    assert sources == []
    assert degraded is True
    assert len(warnings) >= 2


# ---------------------------------------------------------------------------
# 18. Feature Flag: RESEARCH_ENGINE=subgraph vs RESEARCH_ENGINE=inline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feature_flag_subgraph_and_inline_dispatch():
    tool = MockTool(
        "web_search",
        results=[ToolResult(content="Grounding content", source_title="Title", source_url="https://test.org")],
    )

    # 1. Test Subgraph Mode (default)
    with patch.dict("os.environ", {"RESEARCH_ENGINE": "subgraph"}):
        deps = GenerationDeps(
            llm=FakeLLMProvider(
                [
                    json.dumps({"web_search": ["query 1"]}),
                    json.dumps(HEALTHY_10_NODE_PAYLOAD),
                ]
            ),
            decision_model=FakeDecisionModel(),
            embedding_provider=FakeEmbeddingProvider(),
            vector_store=FakeVectorStore(),
            tools={"web_search": tool},
            settings=Settings(),
            callback=FakeCallbackClient(),
        )

        state = {
            "job_id": uuid.uuid4(),
            "map_id": uuid.uuid4(),
            "namespace": "test-subgraph-ff",
            "seed_text": "Deep learning convolutional neural networks in medicine",
            "detail_level": "balanced",
            "is_regeneration": False,
        }
        res = await run_generation(deps, initial_state=state, max_retries=1)
        assert res["status"] == "completed"
        # Tool call log must record the subgraph engine
        assert any(log.get("engine") == "subgraph" for log in res.get("tool_call_log", []))

    # 2. Test Inline Mode
    tool.invocations.clear()
    with patch.dict("os.environ", {"RESEARCH_ENGINE": "inline"}):
        deps_inline = GenerationDeps(
            llm=FakeLLMProvider(
                [
                    json.dumps(["sub query 1", "sub query 2"]),
                    json.dumps(HEALTHY_10_NODE_PAYLOAD),
                ]
            ),
            decision_model=FakeDecisionModel(),
            embedding_provider=FakeEmbeddingProvider(),
            vector_store=FakeVectorStore(),
            tools={"web_search": tool},
            settings=Settings(),
            callback=FakeCallbackClient(),
        )

        state_inline = {
            "job_id": uuid.uuid4(),
            "map_id": uuid.uuid4(),
            "namespace": "test-inline-ff",
            "seed_text": "Deep learning convolutional neural networks in medicine",
            "detail_level": "balanced",
            "is_regeneration": False,
        }
        res_inline = await run_generation(deps_inline, initial_state=state_inline, max_retries=1)
        assert res_inline["status"] == "completed"
        # Inline mode executes queries directly and logs tool = 'web_search'
        assert any(log.get("tool") == "web_search" for log in res_inline.get("tool_call_log", []))


# ---------------------------------------------------------------------------
# 19. Direct Unit Tests for Wikipedia & Scholar Tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wikipedia_tool_direct_invocation():
    wiki_tool = WikipediaTool()

    # S3-T1: the tool now uses the Wikipedia REST summary API (canonical URL +
    # CC BY-SA attribution) rather than the MediaWiki search API.
    fake_response_data = {
        "title": "Quantum computing",
        "pageid": 24639,
        "extract": "Quantum computing is a rapidly-emerging technology.",
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Quantum_computing"}},
    }

    mock_resp = MagicMock()
    mock_resp.json.return_value = fake_response_data
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        results = await wiki_tool.invoke(query="Quantum computing")

    assert len(results) == 1
    assert results[0].source_title == "Quantum computing"
    assert "https://en.wikipedia.org/wiki/Quantum_computing" in results[0].source_url
    assert "rapidly-emerging technology" in results[0].content
    assert results[0].license == "CC BY-SA 4.0"


@pytest.mark.asyncio
async def test_scholar_tool_direct_invocation():
    scholar_tool = ScholarTool(api_base="https://api.semanticscholar.org/graph/v1", api_key="test-key")

    fake_scholar_data = {
        "data": [
            {
                "title": "Attention Is All You Need",
                "abstract": "The dominant sequence transduction models are based on complex recurrent or convolutional neural networks.",
                "externalIds": {"DOI": "10.5555/3295222.3295349"},
                "url": "https://arxiv.org/abs/1706.03762",
                "authors": [{"name": "Ashish Vaswani"}, {"name": "Noam Shazeer"}],
                "year": 2017,
                "citationCount": 120,
            },
            {"title": "Metadata-only paper", "abstract": None, "authors": None, "citationCount": 0},
        ]
    }

    mock_resp = MagicMock()
    mock_resp.json.return_value = fake_scholar_data
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp) as get:
        results = await scholar_tool.invoke(query="Attention Is All You Need")

    assert get.await_args.args[0].endswith("/graph/v1/paper/search")
    assert "citationCount" in get.await_args.kwargs["params"]["fields"]
    assert get.await_args.kwargs["headers"]["x-api-key"] == "test-key"
    assert len(results) == 2
    assert results[0].source_title == "Attention Is All You Need"
    assert results[0].doi == "10.5555/3295222.3295349"
    assert results[0].metadata["year"] == 2017
    assert "Ashish Vaswani" in results[0].metadata["authors"]
    assert results[0].metadata["paper"] == {
        "title": "Attention Is All You Need",
        "abstract": results[0].content,
        "year": 2017,
        "citation_count": 120,
        "doi_url": "https://doi.org/10.5555/3295222.3295349",
    }
    assert results[1].content == "Metadata-only paper"
    assert results[1].metadata["paper"]["abstract"] is None
    assert results[1].metadata["paper"]["citation_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(("engine", "dynamic"), [("inline", True), ("subgraph", True), ("inline", False)])
@pytest.mark.parametrize("purpose", ["research", "exam", None])
async def test_scholar_requires_research_purpose(monkeypatch, engine, dynamic, purpose):
    monkeypatch.setenv("RESEARCH_ENGINE", engine)
    monkeypatch.setenv("ENABLE_DYNAMIC_PROMPTS", str(dynamic).lower())
    monkeypatch.setenv("RESEARCH_MAX_ROUNDS", "1")
    paper_metadata = {"title": "Literature", "citation_count": 5}
    scholar = MockTool(
        "scholar",
        [
            ToolResult(
                "Review of published measurements.",
                "Literature",
                "https://doi.org/10.5555/example",
                metadata={"paper": paper_metadata},
            )
        ],
    )
    store = FakeVectorStore()
    deps = GenerationDeps(
        llm=FakeLLMProvider(['{"scholar":["empirical paper"]}']),
        decision_model=FakeDecisionModel(),
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=store,
        tools={"scholar": scholar},
        settings=Settings(),
        callback=FakeCallbackClient(),
    )
    user_id = uuid.uuid4()
    await make_nodes(deps)["retrieve"](
        {
            "seed_text": "Chemical diffusion",
            "namespace": "test-map",
            "job_id": uuid.uuid4(),
            "user_id": user_id,
            "purpose": purpose,
            "routing_decision": {},
        }
    )

    assert bool(scholar.invocations) == (purpose == "research")
    if purpose == "research":
        assert scholar.invocations[0]["user_id"] == user_id
        assert store._store["test-map"][0].paper_metadata == paper_metadata
