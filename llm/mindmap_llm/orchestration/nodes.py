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
    build_regenerate_node_system_prompt,
    build_regenerate_node_user_prompt,
    estimate_tokens,
    extract_json_object,
    size_targets,
)
from orchestration.state import GenerationState
from providers.base import Message
from rag.ingestion import ingest_seed_text, ingest_tool_result
from rag.retrieval import rerank_context, retrieve_context
from seed.normalizer import NoUsableSeedContent
from tools.base import ToolError
from validation.schema import (
    ValidationFailure,
    resolve_branch_to_records,
    resolve_to_records,
    validate_branch_draft,
    validate_draft,
)

logger = logging.getLogger(__name__)

DETAIL_LEVELS = ["overview", "balanced", "detailed"]


class NoPriorSeedError(NoUsableSeedContent):
    """Raised when regenerating a map but no prior seed chunks exist in Pinecone."""

    pass


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
        }

    async def retrieve_node(state: GenerationState) -> dict:
        query = state["seed_text"][:2000]
        tool_call_log: list[dict] = []

        for tool_name in ("web_search", "arxiv_search"):
            if not state["routing_decision"].get(tool_name):
                continue
            tool = deps.tools.get(tool_name)
            if tool is None:
                continue
            try:
                results = await tool.invoke(query=query)
            except ToolError as exc:
                logger.warning("Tool %s failed, continuing without it: %s", tool_name, exc)
                tool_call_log.append({"tool": tool_name, "status": "failed", "error": str(exc)})
                continue
            except Exception as exc:
                # Catch unexpected errors from tool adapters (network timeouts, HTTP errors,
                # response parse failures, etc.) — degrade gracefully rather than failing generation.
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

        candidates = await retrieve_context(
            vector_store=deps.vector_store,
            namespace=state["namespace"],
            query=query,
            embedding_provider=deps.embedding_provider,
            top_k=deps.settings.generation.rag_top_k,
        )
        reranked = await rerank_context(
            decision_model=deps.decision_model,
            query=query,
            candidates=candidates,
            keep_top_k=deps.settings.generation.rag_rerank_top_k,
        )

        await deps.callback.progress(40)
        return {"retrieved_chunks": reranked, "tool_call_log": tool_call_log}

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

    async def generate_branch_node(state: GenerationState) -> dict:
        selected_node = state.get("selected_node")
        if selected_node is None:
            raise ValueError("regenerate_node requires state['selected_node']")

        ancestor_labels = state.get("ancestor_labels", [])
        breadcrumb = state.get("breadcrumb_path") or " > ".join([*ancestor_labels, selected_node.label])
        comment = state.get("comment") or state.get("regeneration_instruction")
        branch_nodes = state.get("branch_nodes", [])
        retrieved_chunks = state.get("retrieved_chunks", [])

        system_prompt = build_regenerate_node_system_prompt()
        prompt = build_regenerate_node_user_prompt(
            selected_node_label=selected_node.label,
            selected_node_description=selected_node.description,
            comment=comment,
            ancestor_labels=ancestor_labels,
            breadcrumb_path=breadcrumb,
            branch_context_nodes=branch_nodes,
            supporting_context=retrieved_chunks,
            previous_validation_errors=state.get("validation_errors") or None,
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

    async def validate_branch_node(state: GenerationState) -> dict:
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
