"""
Bounded multi-step research agent subgraph (S3-A2).

Architecture:
  - Plan Queries Node: targeted queries across parallel tools (Tavily, Wikipedia, Scholar).
  - Search Dispatch Node: parallel execution with Redis caching (1-hour TTL) and time budget enforcement.
  - Aggregate & Dedup Node: canonical URL and DOI deduplication with metadata merging.
  - Sufficiency Evaluation Node: bounded loop (maximum 2 search rounds).
  - Finalize Research Node: returns structured sources, tool logs, and degraded-run warnings.

Guarantees:
  - Strict maximum of 2 search rounds (mathematically cannot loop infinitely).
  - Global research timeout budget enforced across all rounds and tools.
  - Graceful degradation: individual tool failures or Redis outages never crash the pipeline.
  - LangSmith tracing instrumentation via @trace_span.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langgraph.graph import END, StateGraph
from orchestration.deps import GenerationDeps
from providers.decision_model import DecisionModelUnavailable
from research.cache import ResearchCache
from research.dedup import deduplicate_sources
from research.models import ResearchSource, ResearchState
from research.planner import plan_targeted_queries
from tools.base import Tool, ToolError, ToolResult
from tools.policy import ResearchTimeBudget
from tracing import annotate_current_span, trace_span

logger = logging.getLogger(__name__)


def map_tool_to_chunk_source(tool_name: str) -> str:
    """Map tool identifier to ChunkSource value."""
    t = tool_name.lower().strip()
    if t == "wikipedia":
        return "wikipedia"
    if t in ("scholar", "arxiv_search", "arxiv"):
        return "scholar"
    return "web_search"


@trace_span("execute_single_query", run_type="tool")
async def execute_tool_query(
    *,
    tool: Tool,
    tool_name: str,
    query: str,
    cache: ResearchCache,
    budget: ResearchTimeBudget,
    user_id: Any | None = None,
) -> tuple[list[ResearchSource], bool, str | None]:
    """Execute a single query with Redis cache lookup and timeout bounding.

    Returns (sources, is_cache_hit, error_message).
    """
    # 1. Check cache first
    cached = await cache.get(tool_name, query)
    if cached is not None:
        return cached, True, None

    # 2. Check if global budget is expired before calling tool
    if budget.is_expired():
        return [], False, f"Research budget expired before executing query {query!r} on {tool_name}"

    # 3. Invoke tool with remaining budget
    try:
        results: list[ToolResult] = await tool.invoke(
            query=query,
            budget=budget,
            user_id=user_id,
        )
        sources: list[ResearchSource] = []
        for r in results:
            src = ResearchSource(
                content=r.content,
                source_title=r.source_title,
                source_url=r.source_url,
                tool=tool_name,
                doi=r.doi,
                authors=r.metadata.get("authors", []),
                published_date=str(r.metadata.get("year")) if r.metadata.get("year") else None,
                metadata=r.metadata | ({"attribution": r.attribution} if r.attribution else {}),
            )
            sources.append(src)

        # 4. Cache successful non-empty results (TTL 3600 seconds)
        if sources:
            await cache.set(tool_name, query, sources)

        return sources, False, None

    except ToolError as exc:
        logger.warning("Tool %s failed for query %r: %s", tool_name, query, exc)
        return [], False, f"Tool {tool_name!r} failed for query {query!r}: {exc}"
    except Exception as exc:
        logger.warning("Unexpected error from tool %s on query %r: %s", tool_name, query, exc)
        return [], False, f"Tool {tool_name!r} encountered unexpected error: {exc}"


def build_research_subgraph(
    deps: GenerationDeps,
    cache: ResearchCache,
    budget: ResearchTimeBudget,
):
    """Build the LangGraph StateGraph for the research agent subgraph."""

    @trace_span("research_planner_node", run_type="chain")
    async def plan_queries_node(state: ResearchState) -> dict:
        if budget.is_expired():
            return {
                "timed_out": True,
                "warnings": state.get("warnings", [])
                + [f"Research time budget of {budget.budget_s:.1f}s expired during query planning."],
            }

        seed = state.get("seed_text", "")
        active_tools = state.get("active_tools", ["web_search", "wikipedia", "scholar"])
        current_round = state.get("current_round", 1)
        executed_queries = state.get("executed_queries", [])
        feedback = state.get("sufficiency_reason") if current_round > 1 else None

        planned = await plan_targeted_queries(
            llm=deps.llm,
            seed_text=seed,
            active_tools=active_tools,
            current_round=current_round,
            executed_queries=executed_queries,
            sufficiency_feedback=feedback,
            max_queries_per_tool=deps.settings.generation.research_queries_per_tool,
        )

        all_new_queries: list[str] = []
        for q_list in planned.values():
            all_new_queries.extend(q_list)

        logger.info(
            "Research round %d: planned %d queries across %d tools: %s",
            current_round,
            len(all_new_queries),
            len(planned),
            planned,
        )

        return {
            "queries_by_tool": planned,
            "executed_queries": executed_queries + all_new_queries,
        }

    @trace_span("research_dispatch_node", run_type="tool")
    async def dispatch_search_node(state: ResearchState) -> dict:
        queries_by_tool = state.get("queries_by_tool", {})
        warnings: list[str] = list(state.get("warnings", []))
        tool_failures: list[str] = list(state.get("tool_failures", []))
        cache_hits: int = state.get("cache_hits", 0)
        raw_sources: list[ResearchSource] = list(state.get("raw_sources", []))

        # Check budget before launching parallel batch
        if budget.is_expired():
            warnings.append(f"Research time budget of {budget.budget_s:.1f}s expired before tool dispatch.")
            return {
                "timed_out": True,
                "degraded": True,
                "warnings": warnings,
            }

        # Build list of async tasks for parallel execution
        tasks = []
        meta_info = []

        for tool_name, queries in queries_by_tool.items():
            tool_instance = deps.tools.get(tool_name)
            if not tool_instance:
                # If specific tool not in deps.tools, try web_search fallback or record warning
                fallback_tool = deps.tools.get("web_search")
                if fallback_tool:
                    tool_instance = fallback_tool
                else:
                    msg = f"Tool {tool_name!r} not configured or disabled by policy"
                    warnings.append(msg)
                    tool_failures.append(tool_name)
                    continue

            for q in queries:
                tasks.append(
                    execute_tool_query(
                        tool=tool_instance,
                        tool_name=tool_name,
                        query=q,
                        cache=cache,
                        budget=budget,
                        user_id=state.get("user_id"),
                    )
                )
                meta_info.append((tool_name, q))

        if not tasks:
            return {"warnings": warnings}

        # Execute searches concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for (tool_name, query), res in zip(meta_info, results):
            if isinstance(res, BaseException):
                msg = f"Tool {tool_name!r} query {query!r} crashed: {res}"
                warnings.append(msg)
                tool_failures.append(tool_name)
            else:
                sources, is_hit, err = res
                if is_hit:
                    cache_hits += 1
                if err:
                    warnings.append(err)
                    tool_failures.append(tool_name)
                if sources:
                    raw_sources.extend(sources)

        degraded = bool(tool_failures) or state.get("degraded", False)

        return {
            "raw_sources": raw_sources,
            "cache_hits": cache_hits,
            "tool_failures": tool_failures,
            "degraded": degraded,
            "warnings": warnings,
        }

    @trace_span("research_aggregate_and_dedup_node", run_type="parser")
    async def aggregate_and_dedup_node(state: ResearchState) -> dict:
        raw_sources = state.get("raw_sources", [])
        deduped = deduplicate_sources(raw_sources)
        logger.info(
            "Research aggregation: %d raw sources -> %d unique deduplicated sources",
            len(raw_sources),
            len(deduped),
        )
        return {"deduped_sources": deduped}

    @trace_span("research_evaluate_sufficiency_node", run_type="chain")
    async def evaluate_sufficiency_node(state: ResearchState) -> dict:
        current_round = state.get("current_round", 1)
        max_rounds = state.get("max_rounds", 2)
        deduped = state.get("deduped_sources", [])
        warnings = list(state.get("warnings", []))

        # Check 1: Global budget expired -> stop immediately with degraded warning
        if budget.is_expired():
            msg = (
                f"Research budget expired ({budget.elapsed_s:.1f}s elapsed >= {budget.budget_s:.1f}s budget); stopping."
            )
            logger.info(msg)
            warnings.append(msg)
            return {
                "is_sufficient": True,
                "timed_out": True,
                "degraded": True,
                "warnings": warnings,
                "sufficiency_reason": "Global timeout reached.",
            }

        # Check 2: Max rounds reached -> must terminate
        if current_round >= max_rounds:
            logger.info("Maximum research rounds (%d) reached. Proceeding to completion.", max_rounds)
            return {
                "is_sufficient": True,
                "sufficiency_reason": f"Maximum search rounds ({max_rounds}) completed.",
            }

        # Check 3: Zero sources retrieved in round 1 -> needs another round
        if not deduped:
            logger.info("Round %d returned 0 sources; triggering round %d.", current_round, current_round + 1)
            return {
                "is_sufficient": False,
                "current_round": current_round + 1,
                "sufficiency_reason": "No sources retrieved in previous round; broader keywords required.",
            }

        # Check 4: Content sufficiency evaluation
        # Fast heuristic: if we have >= 6 diverse sources across multiple tools, consider sufficient
        tools_represented = {s.tool for s in deduped}
        if len(deduped) >= 6 and len(tools_represented) >= 2:
            return {
                "is_sufficient": True,
                "sufficiency_reason": f"Sufficient grounding: {len(deduped)} sources across {len(tools_represented)} tools.",
            }

        # LLM / Decision model sufficiency classification
        summary = "\n".join(f"- [{s.tool}] {s.source_title}: {s.content[:150]}" for s in deduped[:5])
        eval_state = f"Seed topic: {state.get('seed_text', '')[:500]}\nRetrieved Sources:\n{summary}"

        try:
            res = await deps.decision_model.classify(
                state=eval_state[:2000],
                question="Are these retrieved sources sufficient to build a comprehensive mind map covering core concepts, background, and applications?",
                labels=["sufficient", "insufficient"],
            )
            is_suff = res.label.strip().lower() == "sufficient"
        except (DecisionModelUnavailable, Exception):
            # Graceful fallback: treat >= 3 sources as sufficient
            is_suff = len(deduped) >= 3

        if is_suff:
            return {
                "is_sufficient": True,
                "sufficiency_reason": "Decision model classified retrieved context as sufficient.",
            }
        else:
            return {
                "is_sufficient": False,
                "current_round": current_round + 1,
                "sufficiency_reason": "Gaps identified in subtopics or empirical grounding.",
            }

    @trace_span("research_finalize_node", run_type="chain")
    async def finalize_research_node(state: ResearchState) -> dict:
        annotate_current_span(
            tags=["research_subgraph"],
            metadata={
                "unique_sources": len(state.get("deduped_sources", [])),
                "cache_hits": state.get("cache_hits", 0),
                "rounds": state.get("current_round", 1),
                "degraded": state.get("degraded", False),
            },
        )
        return {}

    def _sufficiency_router(state: ResearchState) -> str:
        """Route to another round if insufficient and under max rounds, else finalize."""
        if state.get("timed_out") or state.get("is_sufficient", True):
            return "finalize_research"
        if state.get("current_round", 1) <= state.get("max_rounds", 2):
            return "plan_queries"
        return "finalize_research"

    graph = StateGraph(ResearchState)
    graph.add_node("plan_queries", plan_queries_node)
    graph.add_node("dispatch_search", dispatch_search_node)
    graph.add_node("aggregate_and_dedup", aggregate_and_dedup_node)
    graph.add_node("evaluate_sufficiency", evaluate_sufficiency_node)
    graph.add_node("finalize_research", finalize_research_node)

    graph.set_entry_point("plan_queries")
    graph.add_edge("plan_queries", "dispatch_search")
    graph.add_edge("dispatch_search", "aggregate_and_dedup")
    graph.add_edge("aggregate_and_dedup", "evaluate_sufficiency")
    graph.add_conditional_edges(
        "evaluate_sufficiency",
        _sufficiency_router,
        {
            "plan_queries": "plan_queries",
            "finalize_research": "finalize_research",
        },
    )
    graph.add_edge("finalize_research", END)

    return graph.compile()


@trace_span("run_research_subgraph", run_type="chain")
async def run_research_subgraph(
    deps: GenerationDeps,
    *,
    seed_text: str,
    namespace: str,
    job_id: Any,
    user_id: Any | None = None,
    active_tools: list[str] | None = None,
    cache: ResearchCache | None = None,
    timeout_s: float | None = None,
    max_rounds: int = 2,
) -> tuple[list[ResearchSource], list[str], bool, list[dict]]:
    """Execute the bounded research agent subgraph.

    Returns:
      (deduped_sources, warnings, degraded, tool_call_log)
    """
    effective_timeout = timeout_s or deps.settings.tools.research_subgraph_timeout_s
    budget = ResearchTimeBudget(budget_s=effective_timeout)
    active_cache = cache or ResearchCache()

    # Determine default active tools
    tools_to_use: list[str]
    if active_tools is None:
        tools_to_use = [str(t) for t in ("web_search", "wikipedia", "scholar") if t in deps.tools]
        if not tools_to_use and "arxiv_search" in deps.tools:
            tools_to_use.append("arxiv_search")
    else:
        tools_to_use = [str(t) for t in active_tools]

    subgraph = build_research_subgraph(deps, cache=active_cache, budget=budget)

    initial_state: ResearchState = {
        "seed_text": seed_text,
        "namespace": namespace,
        "job_id": job_id,
        "user_id": user_id,
        "active_tools": tools_to_use,
        "max_rounds": max_rounds,
        "current_round": 1,
        "queries_by_tool": {},
        "executed_queries": [],
        "raw_sources": [],
        "deduped_sources": [],
        "is_sufficient": False,
        "sufficiency_reason": "",
        "degraded": False,
        "warnings": [],
        "cache_hits": 0,
        "tool_failures": [],
        "timed_out": False,
    }

    try:
        final_state = await subgraph.ainvoke(initial_state)
    except Exception as exc:
        logger.exception("Research subgraph raised unexpected exception: %s", exc)
        return [], [f"Research subgraph error: {exc}"], True, []

    deduped = final_state.get("deduped_sources", [])
    warnings = final_state.get("warnings", [])
    degraded = final_state.get("degraded", False)

    # Format tool_call_log for downstream tracking — must satisfy ToolCallLogEntry schema
    # (requires `tool` and `status`; additional fields are allowed by the dict path in nodes.py)
    executed = final_state.get("executed_queries", [])
    tool_call_log = [
        {
            "tool": "research_subgraph",
            "status": "failed" if degraded and not deduped else "ok",
            "engine": "subgraph",
            "result_count": len(deduped),
            "executed_queries_count": len(executed),
            "sources_found": len(deduped),
            "cache_hits": final_state.get("cache_hits", 0),
            "rounds": final_state.get("current_round", 1),
            "degraded": degraded,
        }
    ]

    return deduped, warnings, degraded, tool_call_log
