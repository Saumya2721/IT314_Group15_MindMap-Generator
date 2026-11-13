from __future__ import annotations

from typing import Literal, TypedDict
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
    routing_decision: dict[str, bool]  # e.g. {"web_search": True, "arxiv_search": False}
    retrieved_chunks: list[RetrievedChunk]
    tool_call_log: list[dict]
    draft_payload: dict | None  # raw LLM JSON output, pre-validation
    validation_errors: list[str]
    retry_count: int
    _max_retries: int
    nodes: list[NodeDraft]
    edges: list[EdgeDraft]

    # --- terminal outcome ---
    status: Literal["completed", "failed"]
    error_message: str | None
    failed_stage: str | None
