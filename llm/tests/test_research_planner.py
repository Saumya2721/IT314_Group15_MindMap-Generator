from __future__ import annotations

import pytest
from providers.base import ToolSpec
from tools.base import Tool, ToolError, ToolResult
from tools.research_planner import execute_research_queries, plan_research_queries

from tests.conftest import FakeLLMProvider


class MockSearchTool(Tool):
    def __init__(self, responses: dict[str, list[ToolResult]] | None = None, fail_queries: set[str] | None = None):
        self.responses = responses or {}
        self.fail_queries = fail_queries or set()
        self.invoked_queries: list[str] = []

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="mock_search", description="Mock tool for tests", parameters={})

    async def invoke(self, **kwargs) -> list[ToolResult]:
        query = str(kwargs.get("query", ""))
        self.invoked_queries.append(query)
        if query in self.fail_queries:
            raise ToolError(f"Simulated search rate limit on {query}")
        return self.responses.get(
            query,
            [
                ToolResult(
                    content=f"Result for {query}",
                    source_title=f"Title: {query}",
                    source_url=f"https://test.org/{query}",
                )
            ],
        )


class ExplodingLLM(FakeLLMProvider):
    async def generate(self, *args, **kwargs):
        raise RuntimeError("LLM network timeout")


@pytest.mark.asyncio
async def test_plan_research_queries_successful_json_parse():
    llm = FakeLLMProvider(['["quantum key distribution", "bb84 protocol", "e91 protocol"]'])
    queries = await plan_research_queries(
        llm=llm,
        seed_text="Quantum cryptography overview",
        n_queries=3,
    )
    assert queries == ["quantum key distribution", "bb84 protocol", "e91 protocol"]


@pytest.mark.asyncio
async def test_plan_research_queries_with_markdown_fences():
    llm = FakeLLMProvider(['```json\n["photosynthesis light reactions", "calvin cycle steps"]\n```'])
    queries = await plan_research_queries(
        llm=llm,
        seed_text="Photosynthesis biological pathway",
        n_queries=2,
    )
    assert queries == ["photosynthesis light reactions", "calvin cycle steps"]


@pytest.mark.asyncio
async def test_plan_research_queries_caps_to_n():
    llm = FakeLLMProvider(['["q1", "q2", "q3", "q4", "q5"]'])
    queries = await plan_research_queries(
        llm=llm,
        seed_text="Some topic",
        n_queries=2,
    )
    assert len(queries) == 2
    assert queries == ["q1", "q2"]


@pytest.mark.asyncio
async def test_plan_research_queries_line_fallback():
    llm = FakeLLMProvider(
        ["1. Machine learning foundations\n2. Supervised learning models\n3. Unsupervised clustering"]
    )
    queries = await plan_research_queries(
        llm=llm,
        seed_text="Machine learning basics",
        n_queries=3,
    )
    assert len(queries) == 3
    assert queries == ["Machine learning foundations", "Supervised learning models", "Unsupervised clustering"]


@pytest.mark.asyncio
async def test_plan_research_queries_fallback_on_llm_failure():
    llm = ExplodingLLM([])
    seed = "Short concept text for fallback test"
    queries = await plan_research_queries(
        llm=llm,
        seed_text=seed,
        n_queries=3,
    )
    assert queries == [seed[:200]]


@pytest.mark.asyncio
async def test_plan_research_queries_fallback_on_empty_response():
    llm = FakeLLMProvider([""])
    seed = "Deep learning transformers"
    queries = await plan_research_queries(
        llm=llm,
        seed_text=seed,
        n_queries=3,
    )
    assert queries == [seed]


@pytest.mark.asyncio
async def test_execute_research_queries_enforces_max_queries_cap():
    tool = MockSearchTool()
    queries = ["q1", "q2", "q3", "q4", "q5"]
    results = await execute_research_queries(
        tool=tool,
        queries=queries,
        max_queries=3,
    )
    # Only the first 3 queries should have been invoked
    assert tool.invoked_queries == ["q1", "q2", "q3"]
    assert len(results) == 3


@pytest.mark.asyncio
async def test_execute_research_queries_deduplicates_by_url():
    shared_url = "https://example.com/shared"
    tool = MockSearchTool(
        responses={
            "q1": [
                ToolResult(content="Content 1", source_title="T1", source_url=shared_url),
                ToolResult(content="Content 2", source_title="T2", source_url="https://example.com/unique1"),
            ],
            "q2": [
                ToolResult(content="Duplicate Content 1", source_title="T1", source_url=shared_url),
                ToolResult(content="Content 3", source_title="T3", source_url="https://example.com/unique2"),
            ],
        }
    )
    results = await execute_research_queries(
        tool=tool,
        queries=["q1", "q2"],
        max_queries=2,
    )
    assert len(results) == 3
    urls = [r.source_url for r in results]
    assert urls == [shared_url, "https://example.com/unique1", "https://example.com/unique2"]


@pytest.mark.asyncio
async def test_execute_research_queries_handles_partial_tool_failures():
    tool = MockSearchTool(
        fail_queries={"bad_query"},
        responses={
            "good_query": [ToolResult(content="Good Content", source_title="GT", source_url="https://example.com/good")]
        },
    )
    results = await execute_research_queries(
        tool=tool,
        queries=["bad_query", "good_query"],
        max_queries=2,
    )
    # Should not raise exception; should successfully collect good_query results
    assert len(results) == 1
    assert results[0].source_url == "https://example.com/good"


@pytest.mark.asyncio
async def test_execute_research_queries_empty_queries():
    tool = MockSearchTool()
    results = await execute_research_queries(
        tool=tool,
        queries=[],
        max_queries=3,
    )
    assert results == []
    assert tool.invoked_queries == []
