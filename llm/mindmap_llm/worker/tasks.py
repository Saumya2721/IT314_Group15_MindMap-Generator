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

from callback.client import CallbackAlreadySent, JobCallbackClient
from contracts.models import (
    GenerationError,
    GenerationRequest,
    GenerationResult,
    JobType,
    RejectedFile,
    ToolCallLogEntry,
)
from orchestration.deps import GenerationDeps
from orchestration.graph import run_generation
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
        job_id=request.job_id, base_url=request.callback_base_url, token=request.callback_token
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
            job_id=request.job_id, map_id=request.map_id, status="rejected",
            error=GenerationError(stage="intake", error_type=type(exc).__name__, message=str(exc), retryable=False),
        )
        await _send_terminal(callback, result)
    except Exception as exc:  # noqa: BLE001 - this is THE catch-all; see module docstring
        logger.exception("job %s: unhandled exception during generation", request.job_id)
        result = GenerationResult(
            job_id=request.job_id, map_id=request.map_id, status="failed",
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
        },
        max_retries=deps.settings.generation.max_generation_retries,
    )

    if final_state.get("status") == "completed":
        return GenerationResult(
            job_id=request.job_id, map_id=request.map_id, status="completed",
            resolved_detail_level=final_state.get("resolved_detail_level"),
            nodes=final_state.get("nodes", []),
            edges=final_state.get("edges", []),
            seed_assets=seed_assets,
            rejected_files=rejected_files,
            tool_call_log=[ToolCallLogEntry(**e) for e in final_state.get("tool_call_log", [])],
        )

    return GenerationResult(
        job_id=request.job_id, map_id=request.map_id, status="failed",
        seed_assets=seed_assets, rejected_files=rejected_files,
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
