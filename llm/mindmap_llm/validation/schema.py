"""
Validates the LLM's draft output BEFORE it is ever sent back to the
backend over the callback. The backend's own DB CHECK constraints and
triggers (mindmap_schema_supabase.sql section 11) are the last line of
defense; this module is the first, and it's also where domain
requirements that aren't expressible as a single-table CHECK constraint
get enforced:

  DR-01 Graph-based representation  -> node/edge shape below
  DR-02 Single root concept         -> _validate_single_root
  DR-03 Unique node identifiers     -> _validate_unique_ids
  DR-04 Relationship information    -> edges carry a label where the LLM
                                        can supply one (schema allows NULL,
                                        so this is a soft check, not hard)

Node/edge "id" fields here are the LLM's *provisional* string ids
(e.g. "n1", "n2") — resolve_to_records() resolves them to real UUIDs
once a draft passes validation. CHANGED in the worker restructure:
resolve_to_records() now returns contracts.models.NodeDraft/EdgeDraft
(the wire contract to the backend) instead of DB-ready NodeRecord/
EdgeRecord — the worker never inserts these anywhere itself.
"""

from __future__ import annotations

import re
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from contracts.models import EdgeDraft, NodeDraft

HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


class ValidationFailure(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


class _DraftNode(BaseModel):
    """The LLM's raw node shape, pre-UUID-resolution. Prefixed with `_`
    to avoid colliding with contracts.models.NodeDraft (the post-
    resolution, wire-contract shape) — same concept, different stage."""

    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=255)
    description: str | None = None
    background_color: str | None = None
    text_color: str | None = None
    node_type: str = "default"

    @field_validator("background_color", "text_color")
    @classmethod
    def _valid_hex(cls, v: str | None) -> str | None:
        if v is not None and not HEX_COLOR_RE.match(v):
            raise ValueError(f"{v!r} is not a #RRGGBB hex color")
        return v


class _DraftEdge(BaseModel):
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    label: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _no_self_loop(self) -> "_DraftEdge":
        if self.source == self.target:
            raise ValueError(f"Edge from {self.source!r} to itself is not allowed")
        return self


class DraftMindMap(BaseModel):
    nodes: list[_DraftNode]
    edges: list[_DraftEdge]

    @model_validator(mode="after")
    def _structure(self) -> "DraftMindMap":
        errors: list[str] = []
        errors += _validate_unique_ids(self.nodes)
        errors += _validate_edges_reference_existing_nodes(self.nodes, self.edges)
        errors += _validate_single_root(self.nodes, self.edges)
        if errors:
            raise ValueError("; ".join(errors))
        return self


def _validate_unique_ids(nodes: list[_DraftNode]) -> list[str]:
    seen: set[str] = set()
    errors = []
    for n in nodes:
        if n.id in seen:
            errors.append(
                f"Duplicate node id {n.id!r} (violates DR-03: unique node identification)"
            )
        seen.add(n.id)
    if not nodes:
        errors.append("A mind map must contain at least one node")
    return errors


def _validate_edges_reference_existing_nodes(
    nodes: list[_DraftNode], edges: list[_DraftEdge]
) -> list[str]:
    ids = {n.id for n in nodes}
    errors = []
    for e in edges:
        if e.source not in ids:
            errors.append(f"Edge references unknown source node {e.source!r}")
        if e.target not in ids:
            errors.append(f"Edge references unknown target node {e.target!r}")
    return errors


def _validate_single_root(
    nodes: list[_DraftNode], edges: list[_DraftEdge]
) -> list[str]:
    """DR-02: exactly one central/root node. There is no is_root column
    in the schema, so "root" is structural: the one node with no
    incoming edges, from which every other node is reachable."""
    if not nodes:
        return []
    ids = [n.id for n in nodes]
    incoming: dict[str, int] = {i: 0 for i in ids}
    adjacency: dict[str, list[str]] = {i: [] for i in ids}
    for e in edges:
        if e.target in incoming:
            incoming[e.target] += 1
        if e.source in adjacency and e.target in adjacency:
            adjacency[e.source].append(e.target)
            adjacency[e.target].append(e.source)

    roots = [i for i, count in incoming.items() if count == 0]
    errors: list[str] = []
    if len(roots) == 0:
        errors.append(
            "No root node found — every node has an incoming edge (cycle with no entry point)"
        )
        return errors
    if len(roots) > 1:
        errors.append(
            f"Found {len(roots)} candidate root nodes {roots!r} — DR-02 requires exactly one central node"
        )
        return errors

    root = roots[0]
    visited: set[str] = set()
    stack = [root]
    while stack:
        cur = stack.pop()
        if cur in visited:
            continue
        visited.add(cur)
        stack.extend(adjacency.get(cur, []))
    unreachable = set(ids) - visited
    if unreachable:
        errors.append(
            f"Nodes {sorted(unreachable)} are disconnected from the root {root!r}"
        )
    return errors


def validate_draft(payload: dict) -> DraftMindMap:
    """Raises ValidationFailure with all collected errors, or returns a
    validated DraftMindMap. Callers in orchestration/nodes.py catch this
    to drive the retry-with-feedback loop."""
    try:
        return DraftMindMap.model_validate(payload)
    except Exception as exc:
        raise ValidationFailure([str(exc)]) from exc


def resolve_to_records(draft: DraftMindMap) -> tuple[list[NodeDraft], list[EdgeDraft]]:
    """Maps the LLM's provisional string ids to real UUIDs and produces
    the wire-contract NodeDraft/EdgeDraft objects that go into
    GenerationResult. No map_id is threaded through here anymore — the
    backend already knows which map a job belongs to from the job row
    itself, so the result doesn't need to repeat it per node/edge."""
    from uuid import UUID

    id_map: dict[str, UUID] = {n.id: uuid4() for n in draft.nodes}

    nodes = [
        NodeDraft(
            id=id_map[n.id],
            label=n.label,
            description=n.description,
            node_type=n.node_type,
            background_color=n.background_color,
            text_color=n.text_color,
        )
        for n in draft.nodes
    ]
    edges = [
        EdgeDraft(
            id=uuid4(),
            source_node_id=id_map[e.source],
            target_node_id=id_map[e.target],
            label=e.label,
        )
        for e in draft.edges
    ]
    return nodes, edges
