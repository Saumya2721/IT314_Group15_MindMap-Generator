"""
The ARQ task function. This is the new entry point — it replaces
api/service.py::GenerationService entirely (that file has been moved to
deprecated/). One task handles both 'generate' and 'regenerate_full'
(job_type on the request decides which path runs); 'regenerate_node' is
not implemented (US-06, not yet built — see llm.md section 15, which
will need updating to reflect this restructure).

Callback discipline: progress() is best-effort (a missed tick doesn't
fail the job — see callback/client.py). complete()/fail() are called
EXACTLY ONCE, from the single try/except block below, so there is no
path where the task finishes without telling the backend something.
That one change is what eliminates the old K1 (stuck 'pending' jobs):
if anything raises, we still land in the `except` branch and still call
callback.fail() before the task returns.
"""

from __future__ import annotations

import logging
import traceback
from uuid import uuid4

from callback.client import CallbackAlreadySent, JobCallbackClient
from chat.agent import run_chat_turn
from contracts.models import (
    ChatTurnRequest,
    GenerationError,
    GenerationRequest,
    GenerationResult,
    JobType,
    NodeDraft,
    RejectedFile,
    ToolCallLogEntry,
)
from orchestration.deps import GenerationDeps
from orchestration.graph import run_generation, run_regenerate_node
from orchestration.nodes import NoPriorSeedError, reconstruct_seed_from_namespace
from seed.normalizer import NoUsableSeedContent, SeedSubmission, normalize_seed
from worker.files import fetch_all

logger = logging.getLogger(__name__)


async def generate_mindmap(ctx: dict, request_payload: dict) -> dict:
    """ARQ entry point. `ctx` carries the shared clients built once per
    worker process in worker/settings.py::on_startup. `request_payload`
    is GenerationRequest.model_dump() — ARQ serializes task args with
    its own (de)serializer, so this function takes/returns plain dicts
    rather than Pydantic models directly."""
    request = GenerationRequest.model_validate(request_payload)
    namespace = str(request.map_id)

    callback = ctx.get("callback") or JobCallbackClient(
        job_id=request.job_id,
        base_url=request.callback_base_url,
        token=request.callback_token,
    )
    deps = GenerationDeps(
        llm=ctx["llm"],
        decision_model=ctx["decision_model"],
        embedding_provider=ctx["embedding_provider"],
        vector_store=ctx["vector_store"],
        tools=ctx["tools"],
        settings=ctx["settings"],
        callback=callback,
    )

    try:
        result = await _run(request, namespace, deps)
    except (NoUsableSeedContent, NoPriorSeedError) as exc:
        # "Nothing to generate from" — a clean reject, not a crash.
        # NoUsableSeedContent: every uploaded file + text was unusable (generate).
        # NoPriorSeedError: no prior seed to regenerate from (reconstruct_seed_from_namespace).
        result = GenerationResult(
            job_id=request.job_id,
            map_id=request.map_id,
            status="rejected",
            error=GenerationError(
                stage="intake",
                error_type=type(exc).__name__,
                message=str(exc),
                retryable=False,
            ),
        )
        await _send_terminal(callback, result)
    except Exception as exc:  # noqa: BLE001 - this is THE catch-all; see module docstring
        logger.exception("job %s: unhandled exception during generation", request.job_id)
        result = GenerationResult(
            job_id=request.job_id,
            map_id=request.map_id,
            status="failed",
            error=GenerationError(
                stage="unknown",
                error_type=type(exc).__name__,
                message=f"{exc}\n\n{traceback.format_exc(limit=5)}",
                retryable=True,
            ),
        )
        await _send_terminal(callback, result)
    else:
        await _send_terminal(callback, result)
    finally:
        await callback.aclose()

    return result.model_dump(mode="json")


async def _run_regenerate_node(
    request: GenerationRequest,
    namespace: str,
    deps: GenerationDeps,
) -> GenerationResult:
    """Core logic for node-level regeneration (S2-L7, S2-L10)."""
    selected_node = request.selected_node
    branch_nodes = request.effective_branch_nodes
    branch_edges = request.effective_branch_edges
    comment = request.effective_comment

    if not selected_node:
        target_id = request.target_node_id
        if target_id and branch_nodes:
            for n in branch_nodes:
                if n.id == target_id:
                    selected_node = n
                    break
        if not selected_node and target_id and isinstance(request.branch_context, dict):
            label = request.branch_context.get("target_node_label") or "Target Node"
            desc = request.branch_context.get("target_node_description")
            selected_node = NodeDraft(
                id=target_id,
                label=label,
                description=desc,
                node_type="default",
            )

    if not selected_node:
        raise ValueError("regenerate_node request must include selected_node")

    ancestor_labels = request.ancestor_labels or []
    breadcrumb = " > ".join([*ancestor_labels, selected_node.label])
    allowed_ids = {str(selected_node.id), *(str(n.id) for n in branch_nodes)}

    final_state = await run_regenerate_node(
        deps,
        initial_state={
            "job_id": request.job_id,
            "map_id": request.map_id,
            "namespace": namespace,
            "selected_node": selected_node,
            "target_node_id": request.target_node_id or selected_node.id,
            "comment": comment,
            "ancestor_labels": ancestor_labels,
            "branch_nodes": branch_nodes,
            "branch_edges": branch_edges,
            "allowed_node_ids": allowed_ids,
            "breadcrumb_path": breadcrumb,
            "is_regeneration": True,
        },
        max_retries=deps.settings.generation.max_generation_retries,
    )

    if final_state.get("status") == "completed":
        return GenerationResult(
            job_id=request.job_id,
            map_id=request.map_id,
            status="completed",
            resolved_detail_level=final_state.get("resolved_detail_level"),
            nodes=final_state.get("nodes", []),
            edges=final_state.get("edges", []),
            seed_assets=[],
            rejected_files=[],
            tool_call_log=[ToolCallLogEntry(**e) for e in final_state.get("tool_call_log", [])],
        )

    return GenerationResult(
        job_id=request.job_id,
        map_id=request.map_id,
        status="failed",
        seed_assets=[],
        rejected_files=[],
        error=GenerationError(
            stage=final_state.get("failed_stage") or "validate",
            error_type="ValidationFailure",
            message=final_state.get("error_message") or "Node regeneration failed for an unknown reason.",
            retryable=True,
        ),
    )


async def regenerate_node_task(ctx: dict, request_payload: dict) -> dict:
    """ARQ entry point for node-level regeneration (S2-L7, S2-L10, US-06)."""
    request = GenerationRequest.model_validate(request_payload)
    request.job_type = JobType.REGENERATE_NODE
    namespace = str(request.map_id)

    callback = ctx.get("callback") or JobCallbackClient(
        job_id=request.job_id,
        base_url=request.callback_base_url,
        token=request.callback_token,
    )
    deps = GenerationDeps(
        llm=ctx["llm"],
        decision_model=ctx["decision_model"],
        embedding_provider=ctx["embedding_provider"],
        vector_store=ctx["vector_store"],
        tools=ctx["tools"],
        settings=ctx["settings"],
        callback=callback,
    )

    try:
        result = await _run_regenerate_node(request, namespace, deps)
    except Exception as exc:  # noqa: BLE001 - catch-all for terminal failure reporting
        logger.exception("job %s: unhandled exception during node regeneration", request.job_id)
        result = GenerationResult(
            job_id=request.job_id,
            map_id=request.map_id,
            status="failed",
            error=GenerationError(
                stage="unknown",
                error_type=type(exc).__name__,
                message=f"{exc}\n\n{traceback.format_exc(limit=5)}",
                retryable=True,
            ),
        )
        await _send_terminal(callback, result)
    else:
        await _send_terminal(callback, result)
    finally:
        await callback.aclose()

    return result.model_dump(mode="json")


async def chat_turn_task(ctx: dict, request_payload: dict) -> dict:
    """ARQ entry point for chat turns (S2-L8, S2-L9, S2-L10, US-14, US-15)."""
    request = ChatTurnRequest.model_validate(request_payload)
    job_id = request.job_id or uuid4()
    request.job_id = job_id

    callback = ctx.get("callback") or JobCallbackClient(
        job_id=job_id,
        base_url=request.callback_base_url,
        token=request.callback_token,
    )
    deps = GenerationDeps(
        llm=ctx["llm"],
        decision_model=ctx["decision_model"],
        embedding_provider=ctx["embedding_provider"],
        vector_store=ctx["vector_store"],
        tools=ctx["tools"],
        settings=ctx["settings"],
        callback=callback,
    )

    try:
        response = await run_chat_turn(deps, request)

        if response.answer:
            assistant_message = response.answer
        elif response.action_proposal:
            assistant_message = response.action_proposal.explanation or "Proposed an action on the map."
        elif response.needs_clarification:
            assistant_message = response.clarification_question or "Could you clarify your request?"
        else:
            assistant_message = "Processed your request."

        await callback.complete_chat(
            map_id=request.map_id,
            assistant_message=assistant_message,
            summary=request.summary,
        )
        return response.model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001 - catch-all for terminal failure reporting
        logger.exception("job %s: unhandled exception during chat turn", job_id)
        error_msg = f"{type(exc).__name__}: {exc}"
        try:
            await callback.fail_chat(
                map_id=request.map_id,
                error=error_msg,
            )
        except Exception as cb_exc:
            logger.error("job %s: fail_chat callback also failed: %s", job_id, cb_exc)
        return {
            "intent": "question",
            "needs_clarification": True,
            "clarification_question": "An error occurred while processing your request. Please try again.",
            "error": error_msg,
        }
    finally:
        await callback.aclose()


async def _run(request: GenerationRequest, namespace: str, deps: GenerationDeps) -> GenerationResult:
    seed_assets = []
    rejected_files: list[RejectedFile] = []

    if request.job_type == JobType.GENERATE:
        fetched_files, fetch_failures = await fetch_all(request.files)
        rejected_files += [RejectedFile(file_name=n, reason=r) for n, r in fetch_failures]

        normalized = await normalize_seed(
            submission=SeedSubmission(topic=request.topic, ideas=request.ideas, files=fetched_files),
            decision_model=deps.decision_model,
        )
        seed_assets = normalized.seed_assets
        rejected_files += [RejectedFile(file_name=n, reason=r) for n, r in normalized.rejected_files]
        seed_text = normalized.combined_text
        is_regeneration = False

    elif request.job_type == JobType.REGENERATE_FULL:
        seed_text = await reconstruct_seed_from_namespace(deps, namespace=namespace)
        is_regeneration = True

    elif request.job_type == JobType.REGENERATE_NODE:
        return await _run_regenerate_node(request, namespace, deps)

    else:
        raise NotImplementedError(f"{request.job_type} is not implemented yet (see llm.md section 15)")

    final_state = await run_generation(
        deps,
        initial_state={
            "job_id": request.job_id,
            "map_id": request.map_id,
            "namespace": namespace,
            "seed_text": seed_text,
            "detail_level": request.detail_level,
            "regeneration_instruction": request.regeneration_instruction,
            "is_regeneration": is_regeneration,
            "purpose": request.purpose,
            "seed_has_files": bool(request.files or request.seed_has_files),
        },
        max_retries=deps.settings.generation.max_generation_retries,
    )

    if final_state.get("status") == "completed":
        return GenerationResult(
            job_id=request.job_id,
            map_id=request.map_id,
            status="completed",
            resolved_detail_level=final_state.get("resolved_detail_level"),
            nodes=final_state.get("nodes", []),
            edges=final_state.get("edges", []),
            seed_assets=seed_assets,
            rejected_files=rejected_files,
            tool_call_log=[ToolCallLogEntry(**e) for e in final_state.get("tool_call_log", [])],
            quality_warnings=final_state.get("quality_warnings", []),
        )

    return GenerationResult(
        job_id=request.job_id,
        map_id=request.map_id,
        status="failed",
        seed_assets=seed_assets,
        rejected_files=rejected_files,
        error=GenerationError(
            stage=final_state.get("failed_stage") or "validate",
            error_type="ValidationFailure",
            message=final_state.get("error_message") or "Generation failed for an unknown reason.",
            retryable=True,
        ),
    )


async def _send_terminal(callback: JobCallbackClient, result: GenerationResult) -> None:
    try:
        if result.status == "completed":
            await callback.progress(100)
            await callback.complete(result)
        else:
            await callback.fail(result)
    except CallbackAlreadySent:
        # Defensive only — _run() returns exactly one terminal result and
        # generate_mindmap() calls _send_terminal exactly once per branch,
        # so this should be unreachable in practice.
        logger.error("job %s: attempted a second terminal callback", result.job_id)
