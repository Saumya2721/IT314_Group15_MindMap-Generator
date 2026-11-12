"""Pydantic schemas for Mind Map Graph Editing API.

Supports:
- Node and Edge CRUD
- Batch graph updates and optimistic concurrency control
- Graph validation endpoints
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from contracts.models import DetailLevel
from pydantic import BaseModel, Field, field_validator


class NodeCreate(BaseModel):
    """Payload to create a new node on the canvas."""

    label: str = Field(
        ..., min_length=1, max_length=255, description="Concept title/label"
    )
    description: str | None = Field(
        None, description="Optional detailed note or definition"
    )
    pos_x: float = Field(0.0, description="Canvas horizontal coordinate")
    pos_y: float = Field(0.0, description="Canvas vertical coordinate")
    background_color: str | None = Field(
        None,
        pattern=r"^#[0-9A-Fa-f]{6}$",
        description="Hex fill color #RRGGBB",
    )
    text_color: str | None = Field(
        None,
        pattern=r"^#[0-9A-Fa-f]{6}$",
        description="Hex font color #RRGGBB",
    )
    width: float | None = Field(None, gt=0, description="Node width in pixels")
    height: float | None = Field(None, gt=0, description="Node height in pixels")
    node_type: str = Field(
        "default", max_length=50, description="Visual node style category"
    )
    node_data: dict[str, Any] | None = Field(
        None, description="Custom extensible metadata"
    )

    parent_node_id: UUID | None = Field(
        None, description="Optional parent node to connect to immediately upon creation"
    )
    edge_label: str | None = Field(
        None, max_length=100, description="Optional relationship label to parent node"
    )


class NodeUpdate(BaseModel):
    """Payload to patch an existing node."""

    label: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    pos_x: float | None = None
    pos_y: float | None = None
    background_color: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    text_color: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    width: float | None = Field(None, gt=0)
    height: float | None = Field(None, gt=0)
    node_type: str | None = Field(None, max_length=50)
    node_data: dict[str, Any] | None = None


class EdgeCreate(BaseModel):
    """Payload to create a new directed relationship between concepts."""

    source_node_id: UUID = Field(..., description="Parent concept UUID")
    target_node_id: UUID = Field(..., description="Child concept UUID")
    label: str | None = Field(None, max_length=100, description="Relationship description")
    edge_data: dict[str, Any] | None = Field(
        None, description="Custom extensible metadata"
    )


class EdgeUpdate(BaseModel):
    """Payload to patch an existing relationship."""

    label: str | None = Field(None, max_length=100)
    edge_data: dict[str, Any] | None = None


class BatchNodeItem(BaseModel):
    id: UUID | None = Field(
        None, description="Client-specified UUID or omitted for auto-generation"
    )
    label: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    pos_x: float = 0.0
    pos_y: float = 0.0
    background_color: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    text_color: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    width: float | None = Field(None, gt=0)
    height: float | None = Field(None, gt=0)
    node_type: str = "default"
    node_data: dict[str, Any] | None = None


class BatchNodeUpdateItem(BaseModel):
    id: UUID = Field(..., description="UUID of node to update")
    label: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    pos_x: float | None = None
    pos_y: float | None = None
    background_color: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    text_color: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    width: float | None = Field(None, gt=0)
    height: float | None = Field(None, gt=0)
    node_type: str | None = Field(None, max_length=50)
    node_data: dict[str, Any] | None = None


class BatchEdgeItem(BaseModel):
    id: UUID | None = Field(
        None, description="Client-specified UUID or omitted for auto-generation"
    )
    source_node_id: UUID
    target_node_id: UUID
    label: str | None = Field(None, max_length=100)
    edge_data: dict[str, Any] | None = None


class BatchEdgeUpdateItem(BaseModel):
    id: UUID = Field(..., description="UUID of edge to update")
    label: str | None = Field(None, max_length=100)
    edge_data: dict[str, Any] | None = None


class BatchGraphUpdateRequest(BaseModel):
    """Atomic multi-operation graph modification supporting optimistic versioning."""

    expected_version: int | None = Field(
        None,
        description=(
            "Current graph version for optimistic concurrency control "
            "(409 Conflict if mismatched)"
        ),
    )

    create_nodes: list[BatchNodeItem] = Field(default_factory=list)
    update_nodes: list[BatchNodeUpdateItem] = Field(default_factory=list)
    delete_node_ids: list[UUID] = Field(default_factory=list)

    create_edges: list[BatchEdgeItem] = Field(default_factory=list)
    update_edges: list[BatchEdgeUpdateItem] = Field(default_factory=list)
    delete_edge_ids: list[UUID] = Field(default_factory=list)

    nodes: list[BatchNodeItem] | None = Field(
        None, description="Optional full nodes set for wholesale graph sync"
    )
    edges: list[BatchEdgeItem] | None = Field(
        None, description="Optional full edges set for wholesale graph sync"
    )

    validate_graph: bool = Field(
        True,
        description=(
            "Whether to run complete graph validation (single root, no cycles, "
            "connected) before committing"
        ),
    )


class ValidateGraphRequest(BaseModel):
    """Payload to dry-run validate a draft graph before saving."""

    nodes: list[BatchNodeItem] | None = None
    edges: list[BatchEdgeItem] | None = None


class NodeResponse(BaseModel):
    id: UUID
    map_id: UUID
    label: str
    description: str | None
    pos_x: float
    pos_y: float
    background_color: str | None
    text_color: str | None
    width: float | None
    height: float | None
    node_type: str
    node_data: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class EdgeResponse(BaseModel):
    id: UUID
    map_id: UUID
    source_node_id: UUID
    target_node_id: UUID
    label: str | None
    edge_data: dict[str, Any] | None
    created_at: datetime


class BatchGraphUpdateResponse(BaseModel):
    status: str = "success"
    map_id: UUID
    version: int
    nodes_count: int
    edges_count: int
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]


class ValidateGraphResponse(BaseModel):
    is_valid: bool
    root_id: UUID | None = None
    node_count: int
    edge_count: int
    errors: list[str] = Field(default_factory=list)


class CommentCreate(BaseModel):
    content: str = Field(
        ..., min_length=1, max_length=5000, description="Comment text or refinement instruction"
    )

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Comment content cannot be empty or pure whitespace")
        return s


class CommentUpdate(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000, description="Updated comment text")

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Comment content cannot be empty or pure whitespace")
        return s


class CommentResponse(BaseModel):
    id: UUID
    map_id: UUID
    node_id: UUID
    user_id: UUID
    content: str
    created_at: datetime
    updated_at: datetime


class NodeRegenerateRequest(BaseModel):
    instruction: str | None = Field(
        default=None,
        min_length=1,
        max_length=2000,
        description="Instruction for branch refinement",
    )
    comment_id: UUID | None = Field(
        default=None,
        description="Optional attached comment to use as the refinement instruction",
    )
    detail_level: DetailLevel | None = Field(
        default=None, description="'overview' | 'balanced' | 'detailed'"
    )


class SnapshotCreate(BaseModel):
    reason: str = Field(
        "Manual snapshot", max_length=255, description="Reason or label for this snapshot"
    )


class SnapshotResponse(BaseModel):
    id: UUID
    map_id: UUID
    trigger_job_id: UUID | None
    target_node_id: UUID | None
    reason: str
    nodes_count: int
    edges_count: int
    created_at: datetime


class SnapshotDetailResponse(SnapshotResponse):
    nodes_snapshot: list[dict[str, Any]] = Field(default_factory=list)
    edges_snapshot: list[dict[str, Any]] = Field(default_factory=list)


class BranchResponse(BaseModel):
    map_id: UUID
    target_node_id: UUID
    descendant_ids: list[UUID]
    node_count: int
    edge_count: int
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    parent_edge: dict[str, Any] | None = None
