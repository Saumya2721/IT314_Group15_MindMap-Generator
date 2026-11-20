"""
The wire contract between the backend and the LLM background worker.
Nothing here touches Postgres directly. These are the models that cross the
queue / callback boundary.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

DetailLevel = Literal["overview", "balanced", "detailed"]


class JobType(StrEnum):
    GENERATE = "generate"
    REGENERATE_FULL = "regenerate_full"
    REGENERATE_NODE = "regenerate_node"
    CHAT_TURN = "chat_turn"


class SeedAssetType(StrEnum):
    PDF = "pdf"
    IMAGE = "image"
    TEXT = "text"
    DOCX = "docx"  # S3-BE7: Office Word document
    PPTX = "pptx"  # S3-BE7: Office PowerPoint presentation
    URL = "url"  # S3-BE7: URL-seeded content


class ChunkSource(StrEnum):
    UPLOADED_SEED = "uploaded_seed"
    WEB_SEARCH = "web_search"
    ARXIV = "arxiv"
    WIKIPEDIA = "wikipedia"  # S3-BE7: Wikipedia article chunk
    SCHOLAR = "scholar"  # S3-BE7: Google Scholar / academic chunk
    PAGE = "page"  # S3-BE7: Generic web page chunk


class BranchContext(BaseModel):
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    branch_nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    branch_edges: list[dict[str, Any]] = Field(default_factory=list)
    target_node_id: UUID | None = None
    target_node_label: str | None = None
    target_node_description: str | None = None
    descendant_ids: list[str] = Field(default_factory=list)
    ancestor_labels: list[str] = Field(default_factory=list)
    parent_edge: dict[str, Any] | None = None
    parent_edges: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------
# Inbound: backend -> queue -> worker
# ---------------------------------------------------------------------


class FileRef(BaseModel):
    """A file the backend already uploaded to Storage. The worker fetches
    bytes from `url` itself — raw bytes never go through the queue."""

    file_name: str
    mime_type: str
    url: str  # signed URL, short-lived
    storage_key: str  # the real bucket path, e.g. "<user_id>/<map_id>/<job_id>/<file>"


class GenerationRequest(BaseModel):
    job_id: UUID
    map_id: UUID
    job_type: JobType

    # Fresh generation (US-03): topic/ideas/files. At least one of
    # topic/ideas/files must end up producing usable text.
    topic: str | None = None
    ideas: str | None = None
    files: list[FileRef] = Field(default_factory=list)

    # Regeneration (US-04): the instruction to apply on top of the
    # ORIGINAL seed. The worker reconstructs the seed itself from the
    # vector store (namespace=map_id, source=uploaded_seed) — the
    # backend does not need to resend seed text.
    regeneration_instruction: str | None = None

    detail_level: DetailLevel | None = None  # None => worker infers it
    purpose: str | None = None
    seed_has_files: bool = False

    # Node-level regeneration (US-06 / S2-BE2 / S2-BE7):
    target_node_id: UUID | None = None
    ancestor_labels: list[str] = Field(default_factory=list)
    branch_context: BranchContext | dict[str, Any] | None = None
    instruction: str | None = None

    # Internal callback auth — see callback/client.py. Not a user secret;
    # scoped to this one job by the backend when it enqueues.
    callback_base_url: str
    callback_token: str

    # Short-term memory context (US-15 / S2-BE4): rolling summary + recent message turns
    memory_context: dict[str, Any] | None = None

    # S3-BE7: Optional URL seed — validated public address, no private/loopback IPs
    seed_url: str | None = None

    @property
    def effective_branch_nodes(self) -> list[dict[str, Any]]:
        if self.branch_context:
            bc = self.branch_context
            if isinstance(bc, dict):
                return bc.get("branch_nodes") or bc.get("nodes") or []
            return getattr(bc, "branch_nodes", None) or getattr(bc, "nodes", None) or []
        return []

    @property
    def effective_ancestor_labels(self) -> list[str]:
        if self.ancestor_labels:
            return self.ancestor_labels
        if self.branch_context:
            bc = self.branch_context
            if isinstance(bc, dict):
                return bc.get("ancestor_labels") or []
            return getattr(bc, "ancestor_labels", []) or []
        return []


# ---------------------------------------------------------------------
# Specialized Inbound Models (S2-BE7)
# ---------------------------------------------------------------------


class RegenerateNodeRequest(BaseModel):
    job_id: UUID
    map_id: UUID
    target_node_id: UUID
    job_type: JobType = JobType.REGENERATE_NODE
    instruction: str | None = None
    existing_node_label: str | None = None
    existing_node_description: str | None = None
    context: str | None = None
    callback_base_url: str
    callback_token: str
    memory_context: dict[str, Any] | None = None


class CreateNodeParam(BaseModel):
    id: UUID | str | None = Field(default=None, description="Provisional identifier or UUID")
    label: str
    description: str | None = None
    pos_x: float = 0.0
    pos_y: float = 0.0


class CreateEdgeParam(BaseModel):
    id: UUID | str | None = Field(default=None, description="Optional edge UUID")
    source_node_id: UUID | str
    target_node_id: UUID | str
    label: str | None = None


class GraphEditParameters(BaseModel):
    """Typed parameters for edit_proposal graph delta actions."""

    update_nodes: list[dict[str, Any]] = Field(
        default_factory=list, description="Nodes to modify: [{id, label, description}]"
    )
    create_nodes: list[CreateNodeParam | dict[str, Any]] = Field(
        default_factory=list, description="New nodes to add: [{id, label, description}]"
    )
    delete_nodes: list[str] = Field(default_factory=list, description="IDs of nodes to remove")
    update_edges: list[dict[str, Any]] = Field(
        default_factory=list, description="Edges to update: [{id, source_node_id, target_node_id, label}]"
    )
    create_edges: list[CreateEdgeParam | dict[str, Any]] = Field(
        default_factory=list, description="New edges to add: [{id, source_node_id, target_node_id, label}]"
    )
    delete_edges: list[str] = Field(default_factory=list, description="IDs of edges to remove")


class ActionProposal(BaseModel):
    """Structured action proposal suggested by the assistant (e.g. regenerate or edit)."""

    action_type: str  # "regenerate_map", "regenerate_node", "edit_node", "edit_proposal", "general_answer"
    target_node_id: UUID | None = None
    target_node_ids: list[UUID] = Field(default_factory=list)
    parameters: GraphEditParameters | dict[str, Any] = Field(default_factory=dict)
    explanation: str = ""
    instruction: str | None = None
    description: str | None = None


class ChatTurnRequest(BaseModel):
    """Inbound chat turn request sent across ARQ queue to worker."""

    job_id: UUID
    map_id: UUID
    job_type: JobType = JobType.CHAT_TURN
    message: str
    thread_id: UUID | None = None
    target_node_id: UUID | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)
    summary: str | None = None
    rolling_summary: str | None = None
    map_snapshot: dict[str, Any] = Field(default_factory=dict)
    context_window: list[dict[str, Any]] = Field(default_factory=list)
    callback_base_url: str
    callback_token: str
    memory_context: dict[str, Any] | None = None


# ---------------------------------------------------------------------
# Outbound: worker -> callback -> backend
# ---------------------------------------------------------------------


class NodeDraft(BaseModel):
    id: UUID  # real UUID, generated by the worker at validation time
    label: str
    description: str | None
    node_type: str = "default"
    background_color: str | None = None  # "#RRGGBB"
    text_color: str | None = None
    sources: list[str] = Field(default_factory=list)


class EdgeDraft(BaseModel):
    id: UUID
    source_node_id: UUID
    target_node_id: UUID
    label: str | None


class SeedAssetDraft(BaseModel):
    """What the backend needs to insert its own seed_assets row. The
    worker does the extraction but never writes the row itself."""

    asset_type: SeedAssetType
    file_name: str | None
    storage_key: str | None
    mime_type: str | None
    content_text: str | None
    file_size_bytes: int | None
    checksum_sha256: str | None


class RejectedFile(BaseModel):
    file_name: str
    reason: str


class ToolCallLogEntry(BaseModel):
    tool: str
    status: Literal["ok", "failed"]
    result_count: int | None = None
    error: str | None = None


class GenerationError(BaseModel):
    """Structured failure info — the backend decides how much of
    `message` to show a user; `retryable` is a hint for whether "try
    again" is a reasonable UI action."""

    stage: str  # which pipeline node failed, e.g. "ingest", "generate", "validate"
    error_type: str  # exception class name
    message: str
    retryable: bool = True


class GenerationResult(BaseModel):
    job_id: UUID
    map_id: UUID
    status: Literal["completed", "failed", "rejected"]

    resolved_detail_level: DetailLevel | None = None
    nodes: list[NodeDraft] = Field(default_factory=list)
    edges: list[EdgeDraft] = Field(default_factory=list)
    seed_assets: list[SeedAssetDraft] = Field(default_factory=list)
    rejected_files: list[RejectedFile] = Field(default_factory=list)
    tool_call_log: list[ToolCallLogEntry] = Field(default_factory=list)
    quality_warnings: list[str] = Field(default_factory=list)

    error: GenerationError | None = None


class RegenerateNodeResult(BaseModel):
    job_id: UUID
    map_id: UUID
    target_node_id: UUID
    status: Literal["completed", "failed", "rejected"]
    replacement_node: NodeDraft | None = None
    new_subnodes: list[NodeDraft] = Field(default_factory=list)
    new_edges: list[EdgeDraft] = Field(default_factory=list)
    error: GenerationError | None = None


class ChatTurnResult(BaseModel):
    """Outbound chat turn result sent back to backend internal callback."""

    job_id: UUID
    map_id: UUID
    status: Literal["completed", "failed", "rejected"]
    reply: str = ""
    cited_node_ids: list[UUID] = Field(default_factory=list)
    action_proposal: ActionProposal | None = None
    assistant_message: str | None = None
    summary: str | None = None
    proposed_action: dict[str, Any] | None = None
    error: GenerationError | None = None
