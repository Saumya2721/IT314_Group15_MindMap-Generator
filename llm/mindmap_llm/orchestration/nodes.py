"""
Each function here is one LangGraph node, built by make_nodes(deps) as a
closure over GenerationDeps. CHANGED in the worker restructure:

  - No `persist` node anymore. This graph never writes a database — it
    only produces a validated GenerationState (nodes/edges/metadata).
    worker/tasks.py reads the final state and builds the
    GenerationResult that goes out over the callback.
  - No `fail` node making a DB call either. Terminal state (status,
    error_message, failed_stage) is just set on the state dict; the
    worker task turns that into GenerationResult.error and calls
    callback.fail() exactly once, after the graph has fully returned.
  - Progress reporting (deps.callback.progress(...)) stays IN the graph,
    at the same five milestones as before, because that's genuinely
    mid-pipeline signal a UI wants live, not a terminal outcome.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from typing import Literal, cast

from contracts.models import ChunkSource
from orchestration.context_budget import ContextBudgetManager
from orchestration.deps import GenerationDeps
from orchestration.expansion import execute_two_pass_generation, expand_branch
from orchestration.policy import SeedMode, plan_research
from orchestration.prompts import (
    DETAIL_LEVEL_QUESTION,
    GENERATION_SYSTEM_PROMPT,
    JSONExtractionError,
    build_generation_system_prompt,
    build_generation_user_prompt,
    estimate_tokens,
    extract_json_object,
    format_seed_content,
    size_targets,
)
from orchestration.state import GenerationState
from providers.base import Message
from rag.ingestion import ingest_seed_text, ingest_tool_result
from rag.retrieval import RetrievedChunk, rerank_context, retrieve_context
from research.cache import ResearchCache
from research.subgraph import run_research_subgraph
from seed.normalizer import NoUsableSeedContent
from tools.base import ToolError
from tools.policy import ResearchTimeBudget
from tools.research_planner import execute_research_queries, plan_research_queries
from tracing import annotate_current_span, trace_span
from validation.schema import (
    DraftMindMap,
    ValidationFailure,
    resolve_branch_to_records,
    resolve_to_records,
    run_quality_checks,
    validate_branch_draft,
    validate_draft,
)

logger = logging.getLogger(__name__)

DETAIL_LEVELS = ["overview", "balanced", "detailed"]


class NoPriorSeedError(NoUsableSeedContent):
    """Raised when regenerating a map but no prior seed chunks exist in Pinecone."""

    pass


def _chunk_source_for_tool(tool_name: str) -> ChunkSource:
    """Map a research tool name to its chunk source (S3-T1 / fixes #184)."""
    if tool_name == "web_search":
        return ChunkSource.WEB_SEARCH
    if tool_name == "arxiv_search":
        return ChunkSource.ARXIV
    return ChunkSource.WIKIPEDIA


def _extract_shingles(words: list[str], n: int) -> list[str]:
    """Extract consecutive n-gram word shingles."""
    if len(words) < n:
        return words
    return [" ".join(words[i : i + n]) for i in range(len(words) - n + 1)]


def _deduplicate_against_seed(
    chunks: list[RetrievedChunk],
    *,
    seed_text: str,
    threshold: float = 0.7,
    shingle_size: int = 1,
) -> list[RetrievedChunk]:
    """Remove retrieved chunks whose substantive content is already substantially
    present in the seed text, preventing redundant context from consuming the token budget.
    Supports unigram word overlap (shingle_size=1) and n-gram shingle containment (shingle_size >= 2).
    """
    clean_seed = seed_text.lower().strip()
    if not clean_seed:
        return chunks

    # Content tokens of length >= 3 to avoid stop-word skew
    raw_seed_words = re.findall(r"\b[a-z0-9]{3,}\b", clean_seed)
    if not raw_seed_words:
        return chunks

    seed_items = set(_extract_shingles(raw_seed_words, shingle_size)) if shingle_size > 1 else set(raw_seed_words)

    surviving: list[RetrievedChunk] = []
    for chunk in chunks:
        raw_chunk_words = re.findall(r"\b[a-z0-9]{3,}\b", chunk.record.chunk_text.lower())
        if not raw_chunk_words:
            surviving.append(chunk)
            continue

        chunk_items = _extract_shingles(raw_chunk_words, shingle_size) if shingle_size > 1 else raw_chunk_words
        if not chunk_items:
            surviving.append(chunk)
            continue

        overlap_count = sum(1 for item in chunk_items if item in seed_items)
        overlap_ratio = overlap_count / len(chunk_items)

        if overlap_ratio >= threshold:
            logger.info(
                "Deduplicating chunk %s (overlap=%.2f >= threshold=%.2f with seed, shingle_size=%d)",
                chunk.record.id,
                overlap_ratio,
                threshold,
                shingle_size,
            )
            continue
        surviving.append(chunk)

    return surviving


def make_nodes(deps: GenerationDeps) -> dict[str, Callable]:

    async def ingest_node(state: GenerationState) -> dict:
        if not state.get("is_regeneration"):
            count = await ingest_seed_text(
                vector_store=deps.vector_store,
                namespace=state["namespace"],
                generation_job_id=state["job_id"],
                seed_text=state["seed_text"],
                embedding_provider=deps.embedding_provider,
            )
            logger.info("Ingested %d seed chunks for job %s", count, state["job_id"])
        await deps.callback.progress(10)
        return {}

    async def classify_routing_node(state: GenerationState) -> dict:
        query_text = state["seed_text"]
        regen_instr = state.get("regeneration_instruction")
        if regen_instr:
            query_text += "\n\n" + regen_instr

        enable_dynamic = deps.settings.generation.enable_dynamic_prompts

        resolved_detail = state.get("detail_level")
        if not resolved_detail:
            question = (
                DETAIL_LEVEL_QUESTION
                if enable_dynamic
                else (
                    "How much detail should this mind map have? "
                    "'overview' = 5-10 nodes high level, "
                    "'balanced' = 10-25 nodes with one sub-level, "
                    "'detailed' = 25-60 nodes multi-level."
                )
            )
            result = await deps.decision_model.classify(
                state=query_text[:4000],
                question=question,
                labels=DETAIL_LEVELS,
            )
            resolved_detail = cast(Literal["overview", "balanced", "detailed"], result.label)
            logger.info("Inferred detail_level=%s (confidence=%.2f)", resolved_detail, result.confidence)

        routing: dict[str, bool] = {"web_search": False, "arxiv_search": False, "wikipedia": False}

        if not enable_dynamic:
            # Legacy Sprint 1 routing and sizing
            if "web_search" in deps.tools:
                r = await deps.decision_model.classify(
                    state=query_text[:4000],
                    question="Would a live web search meaningfully improve grounding for this mind map's content?",
                    labels=["yes", "no"],
                )
                routing["web_search"] = r.label == "yes"
            if "arxiv_search" in deps.tools:
                r = await deps.decision_model.classify(
                    state=query_text[:4000],
                    question="Is this topic academic/technical enough that arXiv papers would add useful grounding?",
                    labels=["yes", "no"],
                )
                routing["arxiv_search"] = r.label == "yes"

            if "wikipedia" in deps.tools:
                r = await deps.decision_model.classify(
                    state=query_text[:4000],
                    question="Would a Wikipedia article on this topic improve grounding of the mind map's content?",
                    labels=["yes", "no"],
                )
                routing["wikipedia"] = r.label == "yes"

            await deps.callback.progress(20)
            return {"resolved_detail_level": resolved_detail, "routing_decision": routing}

        available_tools = set(deps.tools.keys())
        has_files = state.get("seed_has_files", False)
        plan = plan_research(
            seed_text=state["seed_text"],
            has_files=has_files,
            available_tools=available_tools,
            purpose=state.get("purpose"),
            thin_seed_word_threshold=deps.settings.generation.thin_seed_word_threshold,
        )
        logger.info("Research plan: %s", plan.reason)

        if "web_search" in available_tools:
            if plan.web_search is not None:
                routing["web_search"] = plan.web_search
            else:
                r = await deps.decision_model.classify(
                    state=query_text[:4000],
                    question="Would a live web search meaningfully improve grounding for this mind map's content?",
                    labels=["yes", "no"],
                )
                routing["web_search"] = r.label == "yes"

        if "arxiv_search" in available_tools:
            if plan.arxiv_search is not None:
                routing["arxiv_search"] = plan.arxiv_search
            else:
                r = await deps.decision_model.classify(
                    state=query_text[:4000],
                    question="Is this topic academic/technical enough that arXiv papers would add useful grounding?",
                    labels=["yes", "no"],
                )
                routing["arxiv_search"] = r.label == "yes"

        if "wikipedia" in available_tools:
            if plan.wikipedia is not None:
                routing["wikipedia"] = plan.wikipedia
            else:
                routing["wikipedia"] = routing.get("web_search", True)

        if "scholar" in available_tools:
            routing["scholar"] = routing.get("arxiv_search", False) or routing.get("web_search", False)

        targets = size_targets(
            resolved_detail,
            seed_tokens=estimate_tokens(state["seed_text"]),
            mode=plan.mode,
            max_output_tokens=deps.settings.llm.max_output_tokens,
        )

        await deps.callback.progress(20)
        return {
            "resolved_detail_level": resolved_detail,
            "routing_decision": routing,
            "mode": plan.mode,
            "targets": targets,
            "target_node_range": (targets.min_nodes, targets.max_nodes),
            "target_max_depth": targets.max_depth,
        }

    async def retrieve_node(state: GenerationState) -> dict:
        query = state["seed_text"][:2000]
        tool_call_log: list[dict] = []
        enable_dynamic = deps.settings.generation.enable_dynamic_prompts
        wikipedia_sections: list[str] = []

        research_warnings: list[str] = list(state.get("research_warnings", []))
        research_engine = getattr(deps.settings.tools, "research_engine", "subgraph").lower()
        engine_mode = os.environ.get("RESEARCH_ENGINE", research_engine).lower()

        if engine_mode == "subgraph" and enable_dynamic:
            # S3-A2: Bounded Multi-Step Research Agent Subgraph with Redis caching and canonical dedup
            subgraph_tools = [
                t
                for t in ("web_search", "wikipedia", "scholar", "arxiv_search")
                if state.get("routing_decision", {}).get(t, True) and deps.tools.get(t) is not None
            ]
            if subgraph_tools:
                cache = ResearchCache(
                    redis_client=getattr(deps, "redis_client", None),
                    ttl_s=deps.settings.tools.research_cache_ttl_s,
                )
                deduped_sources, r_warnings, degraded, logs = await run_research_subgraph(
                    deps,
                    seed_text=state["seed_text"],
                    namespace=state["namespace"],
                    job_id=state["job_id"],
                    user_id=state.get("user_id"),
                    active_tools=subgraph_tools,
                    cache=cache,
                    timeout_s=deps.settings.tools.research_subgraph_timeout_s,
                    max_rounds=deps.settings.tools.research_max_rounds,
                )
                tool_call_log.extend(logs)
                if r_warnings:
                    research_warnings.extend(r_warnings)

                for src in deduped_sources:
                    if src.tool == "wikipedia":
                        chunk_source = ChunkSource.WIKIPEDIA
                    elif src.tool in ("scholar", "arxiv_search", "arxiv"):
                        chunk_source = ChunkSource.SCHOLAR
                    else:
                        chunk_source = ChunkSource.WEB_SEARCH

                    await ingest_tool_result(
                        vector_store=deps.vector_store,
                        namespace=state["namespace"],
                        generation_job_id=state["job_id"],
                        text=src.content,
                        source=chunk_source,
                        source_title=src.source_title,
                        source_url=src.source_url,
                        embedding_provider=deps.embedding_provider,
                    )
        else:
            # Inline retrieval engine (fallback mode when RESEARCH_ENGINE=inline or dynamic disabled)
            active_tools = [
                t
                for t in ("web_search", "arxiv_search", "wikipedia")
                if state["routing_decision"].get(t) and deps.tools.get(t) is not None
            ]

            if not active_tools:
                pass
            elif not enable_dynamic:
                # Legacy Sprint 1 single-query behavior
                for tool_name in active_tools:
                    tool = deps.tools[tool_name]
                    try:
                        results = await tool.invoke(query=query)
                    except ToolError as exc:
                        logger.warning("Tool %s failed, continuing without it: %s", tool_name, exc)
                        tool_call_log.append({"tool": tool_name, "status": "failed", "error": str(exc)})
                        continue
                    except Exception as exc:
                        logger.warning(
                            "Tool %s raised unexpected error, continuing without it: %s",
                            tool_name,
                            exc,
                            exc_info=True,
                        )
                        tool_call_log.append({"tool": tool_name, "status": "failed", "error": str(exc)})
                        continue

                    source = _chunk_source_for_tool(tool_name)
                    for r in results:
                        await ingest_tool_result(
                            vector_store=deps.vector_store,
                            namespace=state["namespace"],
                            generation_job_id=state["job_id"],
                            text=r.content,
                            source=source,
                            source_title=r.source_title,
                            source_url=r.source_url,
                            license=r.license,
                            attribution=r.attribution,
                            embedding_provider=deps.embedding_provider,
                        )
                        if tool_name == "wikipedia":
                            wikipedia_sections.extend(s for s in (r.sections or []) if s and s.strip())
                tool_call_log.append({"tool": tool_name, "status": "ok", "result_count": len(results)})
            else:
                # S2-L16 / S3-L1: Multi-query research planner with parallel execution, quota protection & research time budget (S3-A2)
                sub_queries = await plan_research_queries(
                    llm=deps.llm,
                    seed_text=state["seed_text"],
                    n_queries=deps.settings.generation.research_queries_per_tool,
                )
                logger.info("Planned %d research sub-queries: %s", len(sub_queries), sub_queries)
                research_budget = ResearchTimeBudget(deps.settings.tools.research_time_budget_s)

                for tool_name in active_tools:
                    tool = deps.tools[tool_name]
                    results = await execute_research_queries(
                        tool=tool,
                        queries=sub_queries,
                        max_queries=deps.settings.generation.max_search_queries_per_tool,
                        budget=research_budget,
                    )

                    source = _chunk_source_for_tool(tool_name)
                    for r in results:
                        await ingest_tool_result(
                            vector_store=deps.vector_store,
                            namespace=state["namespace"],
                            generation_job_id=state["job_id"],
                            text=r.content,
                            source=source,
                            source_title=r.source_title,
                            source_url=r.source_url,
                            license=r.license,
                            attribution=r.attribution,
                            embedding_provider=deps.embedding_provider,
                        )
                        if tool_name == "wikipedia":
                            wikipedia_sections.extend(s for s in (r.sections or []) if s and s.strip())
                    status = "ok" if results else "failed"
                    tool_call_log.append({"tool": tool_name, "status": status, "result_count": len(results)})

        candidates = await retrieve_context(
            vector_store=deps.vector_store,
            namespace=state["namespace"],
            query=query,
            embedding_provider=deps.embedding_provider,
            top_k=deps.settings.generation.rag_top_k,
        )

        # Context budgeting: scale rerank keep_k dynamically for large/small seeds
        seed_len = len(state["seed_text"])
        base_rerank_k = deps.settings.generation.rag_rerank_top_k
        if not enable_dynamic:
            keep_top_k = base_rerank_k
        else:
            if seed_len > 3000:
                keep_top_k = min(base_rerank_k * 2, 16)
            elif seed_len < 300:
                keep_top_k = max(base_rerank_k // 2, 3)
            else:
                keep_top_k = base_rerank_k
            keep_top_k = min(keep_top_k, deps.settings.generation.rag_top_k)

        reranked = await rerank_context(
            decision_model=deps.decision_model,
            query=query,
            candidates=candidates,
            keep_top_k=keep_top_k,
        )

        # Deduplication against seed (deduplicates against what is actually placed into the prompt)
        if enable_dynamic:
            prompt_seed = format_seed_content(state["seed_text"], mode=state.get("mode", "document"))
            surviving_chunks = _deduplicate_against_seed(
                reranked,
                seed_text=prompt_seed,
                threshold=deps.settings.generation.seed_dedup_overlap_threshold,
            )
        else:
            surviving_chunks = reranked

        # Observability: prompt items log
        prompt_items_log = [
            {
                "chunk_index": i,
                "source": (c.record.source.value if hasattr(c.record.source, "value") else str(c.record.source)),
                "section_path": getattr(c.record, "section_path", None),
                "char_length": len(c.record.chunk_text),
                "relevance_score": round(c.relevance_score, 3),
            }
            for i, c in enumerate(surviving_chunks, 1)
        ]
        est_tokens = sum(len(c.record.chunk_text) // 4 for c in surviving_chunks)
        logger.info(
            "job %s: %d prompt items surviving | est. ~%d context tokens",
            state.get("job_id"),
            len(surviving_chunks),
            est_tokens,
        )

        web_search_failed = any(
            call.get("tool") == "web_search" and (call.get("status") == "failed" or call.get("result_count", 0) == 0)
            for call in tool_call_log
        )
        retrieval_warnings = []
        if web_search_failed:
            retrieval_warnings.append(
                "Live web search was active but produced no results; map generated from seed and internal model knowledge."
            )

        await deps.callback.progress(40)
        return {
            "retrieved_chunks": surviving_chunks,
            "tool_call_log": tool_call_log,
            "prompt_items_log": prompt_items_log,
            "seed_length": seed_len,
            "quality_warnings": list(state.get("quality_warnings", [])) + retrieval_warnings + research_warnings,
            "research_warnings": research_warnings,
            # S3-T1 / fixes #184: Wikipedia section hierarchy for the Pass-1 outline
            # generator when the seed is a topic (see generate_node below).
            "wikipedia_sections": wikipedia_sections,
        }

    @trace_span("generate_node", run_type="chain")
    async def generate_node(state: GenerationState) -> dict:
        use_two_pass = state.get("two_pass_enabled", deps.settings.generation.enable_two_pass)
        if use_two_pass:
            targets = state.get("targets")
            target_node_range = state.get("target_node_range") or (
                (targets.min_nodes, targets.max_nodes) if targets else (10, 35)
            )
            target_max_depth = state.get("target_max_depth") or (targets.max_depth if targets else 4)

            try:
                nodes, edges, chunks, warnings = await execute_two_pass_generation(
                    deps,
                    namespace=state["namespace"],
                    seed_text=state["seed_text"],
                    detail_level=state.get("resolved_detail_level"),
                    concurrency_limit=deps.settings.generation.branch_expansion_concurrency,
                    min_branches=deps.settings.generation.min_outline_branches,
                    max_branches=deps.settings.generation.max_outline_branches,
                    target_node_range=target_node_range,
                    target_max_depth=target_max_depth,
                    # S3-T1 / fixes #184: Wikipedia section tree guides the
                    # Pass-1 outline for topic-only seeds.
                    section_headings=state.get("wikipedia_sections") or None,
                )
                await deps.callback.progress(70)
                return {
                    "nodes": nodes,
                    "edges": edges,
                    "retrieved_chunks": chunks,
                    "quality_warnings": warnings,
                    "validation_errors": [],
                    "two_pass_complete": True,
                }
            except ValidationFailure as exc:
                return {"draft_payload": None, "validation_errors": exc.errors}
            except Exception as exc:
                logger.exception("Two-pass generation encountered error: %s", exc)
                return {"draft_payload": None, "validation_errors": [str(exc)]}

        retrieved_chunks = state.get("retrieved_chunks", [])
        enable_dynamic = deps.settings.generation.enable_dynamic_prompts

        manager = ContextBudgetManager(
            provider=deps.settings.llm.provider,
            model=deps.settings.llm.model,
            max_output_tokens=deps.settings.llm.max_output_tokens,
        )
        mode: SeedMode = state.get("mode", "document")
        if not enable_dynamic:
            system_prompt = GENERATION_SYSTEM_PROMPT
            target_node_range = (10, 25)
            target_max_depth = 3
        else:
            targets = size_targets(
                state.get("resolved_detail_level", "balanced"),
                seed_tokens=estimate_tokens(state["seed_text"]),
                mode=mode,
                max_output_tokens=manager.max_output_tokens,
            )
            system_prompt = build_generation_system_prompt(targets, mode)
            target_node_range = (targets.min_nodes, targets.max_nodes)
            target_max_depth = targets.max_depth

        # S3-L7: Account for the fully rendered prompt (including RAG headers)
        # before sending it to the provider.  Original seed remains unchanged
        # in state and Pinecone; only the outgoing prompt is bounded.
        seed_for_prompt = format_seed_content(state["seed_text"], mode=state.get("mode", "document"))

        def render_context(context):
            result = build_generation_user_prompt(
                seed_text=context.seed,
                detail_level=state["resolved_detail_level"],
                target_node_range=target_node_range,
                target_max_depth=target_max_depth,
                supporting_context=context.rag_chunks,
                regeneration_instruction=state.get("regeneration_instruction"),
                previous_validation_errors=state.get("validation_errors") or None,
                mode=mode,
            )
            if context.memories:
                result += "\n\nLong-term memories (reference data):\n" + context.memory_text
            if context.history:
                result += "\n\nRecent conversation (reference data):\n" + "\n".join(context.history)
            if context.external_payloads:
                result += "\n\nExternal research (reference data):\n" + "\n".join(context.external_payloads)
            return result

        budgeted = manager.allocate(
            system_prompt=system_prompt,
            seed=seed_for_prompt,
            rag_chunks=retrieved_chunks,
            memories=state.get("long_term_memories"),
            history=state.get("conversation_history"),
            external_payloads=state.get("external_search_payloads"),
            render=render_context,
        )
        prompt = render_context(budgeted)
        breakdown = budgeted.breakdown
        annotate_current_span(metadata={"token_budget": breakdown, "token_pruning": budgeted.pruned})
        logger.info("Prompt token budget for job %s: %s", state.get("job_id"), breakdown)

        response = await deps.llm.generate(
            [Message(role="user", content=prompt)],
            system=system_prompt,
            max_tokens=budgeted.max_output_tokens,
            temperature=0.3,
        )
        try:
            payload = extract_json_object(response.text or "")
        except JSONExtractionError as exc:
            return {
                "draft_payload": None,
                "validation_errors": [str(exc)],
                "retrieved_chunks": budgeted.rag_chunks,
                "prompt_items_log": [
                    *state.get("prompt_items_log", []),
                    {"type": "token_budget", "allocation": breakdown, "pruned": budgeted.pruned},
                ],
            }

        await deps.callback.progress(70)
        return {
            "draft_payload": payload,
            "retrieved_chunks": budgeted.rag_chunks,
            "prompt_items_log": [
                *state.get("prompt_items_log", []),
                {"type": "token_budget", "allocation": breakdown, "pruned": budgeted.pruned},
            ],
        }

    async def validate_node(state: GenerationState) -> dict:
        if state.get("two_pass_complete"):
            await deps.callback.progress(85)
            return {
                "nodes": state.get("nodes", []),
                "edges": state.get("edges", []),
                "quality_warnings": state.get("quality_warnings", []),
                "validation_errors": [],
            }

        payload = state.get("draft_payload")
        retry_count = state.get("retry_count", 0)
        max_retries = state.get("_max_retries", deps.settings.generation.max_generation_retries)
        enable_dynamic = deps.settings.generation.enable_dynamic_prompts

        # If payload is missing but we have a prior valid draft, preserve it
        if payload is None:
            if state.get("best_valid_draft") is not None:
                draft = state["best_valid_draft"]
                supporting_chunks = state.get("retrieved_chunks", [])
                nodes, edges = resolve_to_records(draft, supporting_context=supporting_chunks)
                return {
                    "nodes": nodes,
                    "edges": edges,
                    "quality_warnings": state.get("quality_warnings", []),
                    "validation_errors": [],
                }
            return {
                "validation_errors": state.get("validation_errors", ["No draft produced"]),
                "retry_count": retry_count + 1,
            }

        try:
            draft = validate_draft(payload)
        except ValidationFailure as exc:
            if state.get("best_valid_draft") is not None and retry_count >= max_retries:
                draft = state["best_valid_draft"]
                supporting_chunks = state.get("retrieved_chunks", [])
                nodes, edges = resolve_to_records(draft, supporting_context=supporting_chunks)
                return {
                    "nodes": nodes,
                    "edges": edges,
                    "quality_warnings": state.get("quality_warnings", []),
                    "validation_errors": [],
                }
            return {"validation_errors": exc.errors, "retry_count": retry_count + 1}

        # If dynamic prompts / quality gate is disabled, skip quality checks (Sprint 1 fallback)
        supporting_chunks = state.get("retrieved_chunks", [])
        if not enable_dynamic:
            nodes, edges = resolve_to_records(draft, supporting_context=supporting_chunks)
            await deps.callback.progress(85)
            return {"nodes": nodes, "edges": edges, "validation_errors": []}

        # Run soft quality gate
        report = run_quality_checks(
            draft,
            target_node_range=state.get("target_node_range", (10, 25)),
            target_max_depth=state.get("target_max_depth", 3),
            supporting_context=supporting_chunks,
        )

        from validation.schema import score_draft

        target_node_range = state.get("target_node_range", (10, 25))
        target_max_depth = state.get("target_max_depth", 3)
        curr_score = score_draft(draft, target_node_range=target_node_range, target_max_depth=target_max_depth)
        prev_best = state.get("best_valid_draft")
        prev_score = (
            score_draft(prev_best, target_node_range=target_node_range, target_max_depth=target_max_depth)
            if prev_best
            else -999.0
        )
        best_draft = draft if curr_score >= prev_score else prev_best

        # F6: Accumulate retrieval warnings and draft quality gate warnings
        existing_warnings = list(state.get("quality_warnings") or [])
        gate_warnings = list(report.warnings or [])
        combined_warnings = list(dict.fromkeys(existing_warnings + gate_warnings))

        # If quality failed and retries remain: retry with feedback
        if not report.passed and retry_count < max_retries:
            logger.info(
                "Quality gate failed (retry %d/%d, score=%.1f): %s",
                retry_count + 1,
                max_retries,
                curr_score,
                report.warnings,
            )
            return {
                "best_valid_draft": best_draft,
                "quality_warnings": combined_warnings,
                "validation_errors": gate_warnings,
                "retry_count": retry_count + 1,
            }

        # Soft gate: accept highest scoring draft (either it passed or retries were exhausted)
        chosen_draft = draft if report.passed else (best_draft or draft)
        assert isinstance(chosen_draft, DraftMindMap)
        if not report.passed:
            logger.warning(
                "Accepting best scoring draft (score=%.1f) despite quality gate warnings after %d retries: %s",
                max(curr_score, prev_score),
                retry_count,
                report.warnings,
            )

        nodes, edges = resolve_to_records(chosen_draft, supporting_context=supporting_chunks)
        await deps.callback.progress(85)
        return {
            "nodes": nodes,
            "edges": edges,
            "quality_warnings": combined_warnings,
            "validation_errors": [],
        }

    async def retrieve_branch_node(state: GenerationState) -> dict:
        selected_node = state.get("selected_node")
        if selected_node is None:
            raise ValueError("regenerate_node requires state['selected_node']")

        ancestor_labels = state.get("ancestor_labels", [])
        breadcrumb = state.get("breadcrumb_path") or " > ".join([*ancestor_labels, selected_node.label])
        comment = state.get("comment") or state.get("regeneration_instruction")
        scoped_query = f"{breadcrumb}: {comment or selected_node.description or selected_node.label}"

        candidates = await retrieve_context(
            vector_store=deps.vector_store,
            namespace=state["namespace"],
            query=scoped_query,
            embedding_provider=deps.embedding_provider,
            top_k=deps.settings.generation.rag_top_k,
        )
        reranked = await rerank_context(
            decision_model=deps.decision_model,
            query=scoped_query,
            candidates=candidates,
            keep_top_k=deps.settings.generation.rag_rerank_top_k,
        )

        await deps.callback.progress(40)
        return {"retrieved_chunks": reranked, "breadcrumb_path": breadcrumb}

    @trace_span("generate_branch_node", run_type="chain")
    async def generate_branch_node(state: GenerationState) -> dict:
        selected_node = state.get("selected_node")
        if selected_node is None:
            raise ValueError("regenerate_node requires state['selected_node']")

        ancestor_labels = state.get("ancestor_labels", [])
        breadcrumb = state.get("breadcrumb_path") or " > ".join([*ancestor_labels, selected_node.label])
        comment = state.get("comment") or state.get("regeneration_instruction")
        branch_nodes = state.get("branch_nodes", [])
        retrieved_chunks = state.get("retrieved_chunks", [])
        allowed_ids = state.get("allowed_node_ids")
        if allowed_ids is None:
            allowed_ids = {str(selected_node.id), *(str(n.id) for n in branch_nodes)}

        # Reuse shared branch expansion engine (S2-L12)
        res = await expand_branch(
            deps,
            namespace=state["namespace"],
            selected_node=selected_node,
            ancestor_labels=ancestor_labels,
            breadcrumb_path=breadcrumb,
            comment=comment,
            parent_edges=state.get("parent_edges"),
            branch_context_nodes=branch_nodes,
            allowed_node_ids=allowed_ids,
            existing_chunks=retrieved_chunks,
            previous_validation_errors=state.get("validation_errors"),
            max_retries=0,  # LangGraph graph controls outer retry loop via _validate_branch_router
        )

        if res.status != "completed" or res.draft is None:
            return {"draft_payload": None, "validation_errors": res.errors}

        await deps.callback.progress(70)
        return {
            "draft_payload": res.draft.model_dump(),
            "nodes": res.nodes,
            "edges": res.edges,
            "branch_result": res,
        }

    async def validate_branch_node(state: GenerationState) -> dict:
        if state.get("branch_result") and state["branch_result"].status == "completed":
            res = state["branch_result"]
            await deps.callback.progress(85)
            return {"nodes": res.nodes, "edges": res.edges, "validation_errors": []}

        payload = state.get("draft_payload")
        retry_count = state.get("retry_count", 0)

        if payload is None:
            return {
                "validation_errors": state.get("validation_errors", ["No draft produced"]),
                "retry_count": retry_count + 1,
            }

        selected_node = state.get("selected_node")
        if selected_node is None:
            raise ValueError("regenerate_node requires state['selected_node']")

        allowed_ids = state.get("allowed_node_ids")
        if allowed_ids is None:
            allowed_ids = {str(selected_node.id), *(str(n.id) for n in state.get("branch_nodes", []))}

        try:
            draft = validate_branch_draft(
                payload,
                selected_node=selected_node,
                allowed_node_ids=allowed_ids,
                ancestor_labels=state.get("ancestor_labels", []),
            )
        except ValidationFailure as exc:
            return {"validation_errors": exc.errors, "retry_count": retry_count + 1}

        nodes, edges = resolve_branch_to_records(
            draft,
            selected_node=selected_node,
            allowed_node_ids=allowed_ids,
            supporting_context=state.get("retrieved_chunks"),
        )
        await deps.callback.progress(85)
        return {"nodes": nodes, "edges": edges, "validation_errors": []}

    async def finalize_success_node(state: GenerationState) -> dict:
        """No I/O — just marks the state terminal. worker/tasks.py reports
        100% and calls callback.complete() after the graph returns."""
        return {"status": "completed"}

    async def finalize_failure_node(state: GenerationState) -> dict:
        errors = state.get("validation_errors") or ["Unknown generation failure"]
        return {
            "status": "failed",
            "error_message": "Generation failed after retries: " + "; ".join(errors),
            "failed_stage": "validate",
        }

    return {
        "ingest": ingest_node,
        "classify_routing": classify_routing_node,
        "retrieve": retrieve_node,
        "generate": generate_node,
        "validate": validate_node,
        "finalize_success": finalize_success_node,
        "finalize_failure": finalize_failure_node,
        "retrieve_branch": retrieve_branch_node,
        "generate_branch": generate_branch_node,
        "validate_branch": validate_branch_node,
    }


@trace_span("reconstruct_seed_from_namespace", run_type="chain")
async def reconstruct_seed_from_namespace(deps: GenerationDeps, *, namespace: str) -> str:
    """Regeneration's replacement for the old load_regeneration_context:
    pulls every uploaded_seed chunk back out of Pinecone (no re-upload,
    no re-embedding — US-04's 'preserve the original seed as base
    context'). Raises if there's nothing to reuse."""
    chunks = await deps.vector_store.fetch_all_for_source(
        namespace=namespace,
        source=ChunkSource.UPLOADED_SEED,
        dimension=deps.embedding_provider.dimension,
    )
    if not chunks:
        raise NoPriorSeedError(
            f"No original seed chunks found in namespace {namespace!r} — cannot regenerate without a seed."
        )
    # Pinecone similarity queries do not return document order. Older records lack
    # chunk_index, so their original order is retained as a best-effort fallback.
    sorted_chunks = sorted(chunks, key=lambda c: (c.chunk_index is None, c.chunk_index or 0))
    reconstructed: list[str] = []
    previous_chunk = None
    for chunk in sorted_chunks:
        current = chunk.chunk_text
        if not current:
            continue
        if previous_chunk is None:
            reconstructed.append(current)
        elif chunk.section_path == previous_chunk.section_path:
            # Trim the exact suffix/prefix overlap, accounting for whitespace
            # stripped from chunk edges by the chunker.
            previous = previous_chunk.chunk_text
            overlap = 0
            for size in range(min(len(previous), len(current)), 0, -1):
                if previous.endswith(current[:size]):
                    overlap = size
                    break
            remainder = current[overlap:]
            if remainder:
                if overlap == 0:
                    reconstructed.append(" " + remainder)
                else:
                    reconstructed.append(remainder)
        else:
            if chunk.section_path and chunk.section_path != "Root" and not current.startswith("#"):
                reconstructed.append(f"\n\n## {chunk.section_path}\n\n" + current)
            else:
                reconstructed.append("\n\n" + current)
        previous_chunk = chunk
    return "".join(reconstructed)
