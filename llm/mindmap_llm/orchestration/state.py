from __future__ import annotations

from typing import Any, Literal, TypedDict
from uuid import UUID

from contracts.models import EdgeDraft, NodeDraft
from orchestration.policy import SeedMode
from orchestration.prompts import SizeTargets
from rag.retrieval import RetrievedChunk

DetailLevel = Literal["overview", "balanced", "detailed"]


class GenerationState(TypedDict, total=False):
    # --- inputs, set once before the graph runs ---
    job_id: UUID
    map_id: UUID
    namespace: str  # Pinecone namespace, == str(map_id)
    seed_text: str
    detail_level: DetailLevel | None  # None => infer via Jev/LLM classify
    regeneration_instruction: str | None  # set only for regenerate_full jobs
    is_regeneration: bool
    purpose: str | None
    seed_has_files: bool
    two_pass_enabled: bool

    # --- node regeneration inputs (S2-L7) ---
    selected_node: NodeDraft | None
    target_node_id: UUID | None
    comment: str | None
    ancestor_labels: list[str]
    branch_nodes: list[NodeDraft]
    branch_edges: list[EdgeDraft]
    allowed_node_ids: set[str]
    breadcrumb_path: str

    # --- working state, populated as the graph runs ---
    resolved_detail_level: DetailLevel
    mode: SeedMode
    targets: SizeTargets
    target_node_range: tuple[int, int]
    target_max_depth: int
    routing_decision: dict[str, bool]  # e.g. {"web_search": True, "arxiv_search": False}
    retrieved_chunks: list[RetrievedChunk]
    tool_call_log: list[dict]
    seed_length: int
    prompt_items_log: list[dict]
    outline_draft: dict | None  # Pass 1 outline payload (S2-L12)
    two_pass_complete: bool
    branch_result: Any
    draft_payload: dict | None  # raw LLM JSON output, pre-validation
    validation_errors: list[str]
    quality_warnings: list[str]
    best_valid_draft: Any
    retry_count: int
    _max_retries: int
    nodes: list[NodeDraft]
    edges: list[EdgeDraft]

    # --- terminal outcome ---
    status: Literal["completed", "failed"]
    error_message: str | None
    failed_stage: str | None
