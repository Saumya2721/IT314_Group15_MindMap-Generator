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
import re
from collections.abc import Callable
from typing import Literal, cast

from contracts.models import ChunkSource
from orchestration.deps import GenerationDeps
from orchestration.policy import SeedMode, plan_research
from orchestration.prompts import (
    DETAIL_LEVEL_QUESTION,
    GENERATION_SYSTEM_PROMPT,
    JSONExtractionError,
    build_generation_system_prompt,
    build_generation_user_prompt,
    estimate_tokens,
    extract_json_object,
    size_targets,
)
from orchestration.state import GenerationState
from providers.base import Message
from rag.ingestion import ingest_seed_text, ingest_tool_result
from rag.retrieval import RetrievedChunk, rerank_context, retrieve_context
from seed.normalizer import NoUsableSeedContent
from tools.base import ToolError
from tools.research_planner import execute_research_queries, plan_research_queries
from validation.schema import ValidationFailure, resolve_to_records, validate_draft

logger = logging.getLogger(__name__)

DETAIL_LEVELS = ["overview", "balanced", "detailed"]


class NoPriorSeedError(NoUsableSeedContent):
    """Raised when regenerating a map but no prior seed chunks exist in Pinecone."""

    pass


def _deduplicate_against_seed(
    chunks: list[RetrievedChunk],
    *,
    seed_text: str,
    threshold: float = 0.6,
) -> list[RetrievedChunk]:
    """Remove retrieved chunks whose substantive content is already substantially
    present in the seed text, preventing redundant context from consuming the token budget.
    """
    clean_seed = seed_text.lower().strip()
    if not clean_seed:
        return chunks

    # Content tokens of length >= 3 to avoid stop-word skew
    seed_words = set(re.findall(r"\b[a-z0-9]{3,}\b", clean_seed))
    if not seed_words:
        return chunks

    surviving: list[RetrievedChunk] = []
    for chunk in chunks:
        chunk_words = re.findall(r"\b[a-z0-9]{3,}\b", chunk.record.chunk_text.lower())
        if not chunk_words:
            surviving.append(chunk)
            continue

        overlap_count = sum(1 for w in chunk_words if w in seed_words)
        overlap_ratio = overlap_count / len(chunk_words)

        if overlap_ratio >= threshold:
            logger.info(
                "Deduplicating chunk %s (overlap=%.2f >= threshold=%.2f with seed)",
                chunk.record.id,
                overlap_ratio,
                threshold,
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

        routing: dict[str, bool] = {"web_search": False, "arxiv_search": False}

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

        active_tools = [
            t
            for t in ("web_search", "arxiv_search")
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

                source = ChunkSource.WEB_SEARCH if tool_name == "web_search" else ChunkSource.ARXIV
                for r in results:
                    await ingest_tool_result(
                        vector_store=deps.vector_store,
                        namespace=state["namespace"],
                        generation_job_id=state["job_id"],
                        text=r.content,
                        source=source,
                        source_title=r.source_title,
                        source_url=r.source_url,
                        embedding_provider=deps.embedding_provider,
                    )
                tool_call_log.append({"tool": tool_name, "status": "ok", "result_count": len(results)})
        else:
            # S2-L16: Multi-query research planner with parallel execution & quota protection
            sub_queries = await plan_research_queries(
                llm=deps.llm,
                seed_text=state["seed_text"],
                n_queries=deps.settings.generation.research_queries_per_tool,
            )
            logger.info("Planned %d research sub-queries: %s", len(sub_queries), sub_queries)

            for tool_name in active_tools:
                tool = deps.tools[tool_name]
                results = await execute_research_queries(
                    tool=tool,
                    queries=sub_queries,
                    max_queries=deps.settings.generation.max_search_queries_per_tool,
                )

                source = ChunkSource.WEB_SEARCH if tool_name == "web_search" else ChunkSource.ARXIV
                for r in results:
                    await ingest_tool_result(
                        vector_store=deps.vector_store,
                        namespace=state["namespace"],
                        generation_job_id=state["job_id"],
                        text=r.content,
                        source=source,
                        source_title=r.source_title,
                        source_url=r.source_url,
                        embedding_provider=deps.embedding_provider,
                    )
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

        # Deduplication against seed
        if enable_dynamic:
            surviving_chunks = _deduplicate_against_seed(
                reranked,
                seed_text=state["seed_text"],
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

        await deps.callback.progress(40)
        return {
            "retrieved_chunks": surviving_chunks,
            "tool_call_log": tool_call_log,
            "prompt_items_log": prompt_items_log,
            "seed_length": seed_len,
        }

    async def generate_node(state: GenerationState) -> dict:
        retrieved_chunks = state.get("retrieved_chunks", [])
        enable_dynamic = deps.settings.generation.enable_dynamic_prompts

        if not enable_dynamic:
            system_prompt = GENERATION_SYSTEM_PROMPT
            prompt = build_generation_user_prompt(
                seed_text=state["seed_text"],
                detail_level=state["resolved_detail_level"],
                target_node_range=(10, 25),
                target_max_depth=3,
                supporting_context=retrieved_chunks,
                regeneration_instruction=state.get("regeneration_instruction"),
                previous_validation_errors=state.get("validation_errors") or None,
            )
        else:
            mode: SeedMode = state.get("mode", "document")
            targets = state.get("targets")
            if targets is None:
                targets = size_targets(
                    state.get("resolved_detail_level", "balanced"),
                    seed_tokens=estimate_tokens(state["seed_text"]),
                    mode=mode,
                    max_output_tokens=deps.settings.llm.max_output_tokens,
                )

            system_prompt = build_generation_system_prompt(targets, mode)
            prompt = build_generation_user_prompt(
                seed_text=state["seed_text"],
                detail_level=state["resolved_detail_level"],
                target_node_range=(targets.min_nodes, targets.max_nodes),
                target_max_depth=targets.max_depth,
                supporting_context=retrieved_chunks,
                regeneration_instruction=state.get("regeneration_instruction"),
                previous_validation_errors=state.get("validation_errors") or None,
                mode=mode,
            )

        response = await deps.llm.generate(
            [Message(role="user", content=prompt)],
            system=system_prompt,
            max_tokens=deps.settings.llm.max_output_tokens,
            temperature=0.3,
        )
        try:
            payload = extract_json_object(response.text or "")
        except JSONExtractionError as exc:
            return {"draft_payload": None, "validation_errors": [str(exc)]}

        await deps.callback.progress(70)
        return {"draft_payload": payload}

    async def validate_node(state: GenerationState) -> dict:
        payload = state.get("draft_payload")
        retry_count = state.get("retry_count", 0)

        if payload is None:
            return {
                "validation_errors": state.get("validation_errors", ["No draft produced"]),
                "retry_count": retry_count + 1,
            }

        try:
            draft = validate_draft(payload)
        except ValidationFailure as exc:
            return {"validation_errors": exc.errors, "retry_count": retry_count + 1}

        nodes, edges = resolve_to_records(draft)
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
    }


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
    sorted_chunks = sorted(chunks, key=lambda c: getattr(c, "chunk_index", 0))
    return "\n\n".join(c.chunk_text for c in sorted_chunks)
