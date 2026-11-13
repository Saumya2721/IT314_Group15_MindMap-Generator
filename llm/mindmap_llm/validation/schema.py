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
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from contracts.models import EdgeDraft, NodeDraft
from pydantic import BaseModel, Field, field_validator, model_validator

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
    sources: list[str] = Field(default_factory=list)

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
    def _no_self_loop(self) -> _DraftEdge:
        if self.source == self.target:
            raise ValueError(f"Edge from {self.source!r} to itself is not allowed")
        return self


class DraftMindMap(BaseModel):
    nodes: list[_DraftNode]
    edges: list[_DraftEdge]

    @model_validator(mode="after")
    def _structure(self) -> DraftMindMap:
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
            errors.append(f"Duplicate node id {n.id!r} (violates DR-03: unique node identification)")
        seen.add(n.id)
    if not nodes:
        errors.append("A mind map must contain at least one node")
    return errors


def _validate_edges_reference_existing_nodes(nodes: list[_DraftNode], edges: list[_DraftEdge]) -> list[str]:
    ids = {n.id for n in nodes}
    errors = []
    for e in edges:
        if e.source not in ids:
            errors.append(f"Edge references unknown source node {e.source!r}")
        if e.target not in ids:
            errors.append(f"Edge references unknown target node {e.target!r}")
    return errors


def _validate_single_root(nodes: list[_DraftNode], edges: list[_DraftEdge]) -> list[str]:
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
        errors.append("No root node found — every node has an incoming edge (cycle with no entry point)")
        return errors
    if len(roots) > 1:
        errors.append(f"Found {len(roots)} candidate root nodes {roots!r} — DR-02 requires exactly one central node")
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
        errors.append(f"Nodes {sorted(unreachable)} are disconnected from the root {root!r}")
    return errors


@dataclass
class QualityReport:
    warnings: list[str]
    passed: bool  # True = meets minimum node count bar


def _compute_tree_depth(nodes: list[_DraftNode], edges: list[_DraftEdge]) -> int:
    """Compute maximum depth of the mind map tree from the root node (root = depth 0)."""
    incoming = {n.id: 0 for n in nodes}
    children: dict[str, list[str]] = {n.id: [] for n in nodes}
    for e in edges:
        if e.target in incoming:
            incoming[e.target] += 1
        if e.source in children:
            children[e.source].append(e.target)

    roots = [n_id for n_id, count in incoming.items() if count == 0]
    if not roots:
        return 0

    max_depth = 0
    stack: list[tuple[str, int]] = [(roots[0], 0)]
    visited: set[str] = set()

    while stack:
        cur_id, depth = stack.pop()
        if cur_id in visited:
            continue
        visited.add(cur_id)
        if depth > max_depth:
            max_depth = depth
        for child_id in children.get(cur_id, []):
            stack.append((child_id, depth + 1))

    return max_depth


def run_quality_checks(
    draft: DraftMindMap,
    *,
    target_node_range: tuple[int, int] = (10, 25),
    target_max_depth: int = 3,
    supporting_context: list[Any] | None = None,
) -> QualityReport:
    """Evaluate content quality beyond syntax: node counts, tree depth,
    branch descriptions, and citation validity.
    """
    warnings: list[str] = []
    min_nodes, max_nodes = target_node_range
    actual_nodes = len(draft.nodes)

    # 1. Node count check (blocks pass if below min)
    passed = True
    if actual_nodes < min_nodes:
        warnings.append(
            f"Mind map node count ({actual_nodes}) is below target minimum ({min_nodes}). Expand branches with more sub-concepts."
        )
        passed = False
    elif actual_nodes > max_nodes:
        warnings.append(f"Mind map node count ({actual_nodes}) exceeds target maximum ({max_nodes}).")

    # 2. Tree depth check
    actual_depth = _compute_tree_depth(draft.nodes, draft.edges)
    if actual_depth > target_max_depth:
        warnings.append(f"Tree depth ({actual_depth}) exceeds target depth ({target_max_depth}).")

    # 3. Branch description check (non-leaf nodes should have explanations)
    children_count = {n.id: 0 for n in draft.nodes}
    for e in draft.edges:
        if e.source in children_count:
            children_count[e.source] += 1

    for n in draft.nodes:
        if children_count[n.id] > 0 and (not n.description or not n.description.strip()):
            warnings.append(f"Branch node '{n.label}' (id: {n.id}) is missing a required 2-3 sentence description.")

    # 4. Citation coverage and validity check
    context_len = len(supporting_context or [])
    has_citations = False
    for n in draft.nodes:
        if n.sources:
            has_citations = True
            for s in n.sources:
                match = re.match(r"^ctx:(\d+)$", s.strip())
                if match:
                    idx = int(match.group(1))
                    if idx < 1 or idx > context_len:
                        warnings.append(
                            f"Node '{n.label}' cites non-existent context item '{s}' (only {context_len} context items provided)."
                        )

    if supporting_context and not has_citations and actual_nodes > 1:
        warnings.append("Supporting context was provided but no nodes include citation sources ('ctx:<n>').")

    return QualityReport(warnings=warnings, passed=passed)


def validate_draft(payload: dict) -> DraftMindMap:
    """Raises ValidationFailure with all collected errors, or returns a
    validated DraftMindMap. Callers in orchestration/nodes.py catch this
    to drive the retry-with-feedback loop."""
    try:
        return DraftMindMap.model_validate(payload)
    except Exception as exc:
        raise ValidationFailure([str(exc)]) from exc


def resolve_to_records(
    draft: DraftMindMap,
    supporting_context: list[Any] | None = None,
) -> tuple[list[NodeDraft], list[EdgeDraft]]:
    """Maps the LLM's provisional string ids to real UUIDs and produces
    the wire-contract NodeDraft/EdgeDraft objects that go into
    GenerationResult. Resolves 'ctx:<n>' citations to actual titles and URLs."""
    id_map: dict[str, UUID] = {n.id: uuid4() for n in draft.nodes}
    context_list = supporting_context or []

    def _resolve_sources(raw_sources: list[str]) -> list[str]:
        resolved: list[str] = []
        for s in raw_sources:
            match = re.match(r"^ctx:(\d+)$", s.strip())
            if match and context_list:
                idx = int(match.group(1)) - 1
                if 0 <= idx < len(context_list):
                    chunk = context_list[idx]
                    title = getattr(chunk.record, "source_title", None)
                    url = getattr(chunk.record, "source_url", None)
                    if title and url:
                        resolved.append(f"{title} ({url})")
                    elif url:
                        resolved.append(url)
                    elif title:
                        resolved.append(title)
                    else:
                        resolved.append(s)
                else:
                    resolved.append(s)
            else:
                resolved.append(s)
        return resolved

    nodes = [
        NodeDraft(
            id=id_map[n.id],
            label=n.label,
            description=n.description,
            node_type=n.node_type,
            background_color=n.background_color,
            text_color=n.text_color,
            sources=_resolve_sources(n.sources),
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


def validate_branch_draft(
    payload: dict,
    *,
    selected_node: NodeDraft,
    allowed_node_ids: set[str] | None = None,
    ancestor_labels: list[str] | None = None,
) -> DraftMindMap:
    """Validates the LLM draft for S2-L7 regenerate_node flow.

    Enforces:
      - Valid DraftMindMap topology (single branch root, connectedness, unique IDs, etc.)
      - Branch root ID preservation: rejects attempts to change the branch root ID.
      - Ancestor immutability: ancestors are read-only; ancestor labels cannot be nodes.
      - Allowed IDs scoping: rejects any node or reference with IDs outside allowed_node_ids.
    """
    draft = validate_draft(payload)
    errors: list[str] = []

    # 1. Identify branch root in draft (in-degree == 0)
    incoming: dict[str, int] = {n.id: 0 for n in draft.nodes}
    for e in draft.edges:
        if e.target in incoming:
            incoming[e.target] += 1
    roots = [n_id for n_id, count in incoming.items() if count == 0]
    if len(roots) != 1:
        errors.append(f"Branch draft must have exactly one root, found {len(roots)}")
        raise ValidationFailure(errors)

    branch_root_id = roots[0]
    selected_node_id_str = str(selected_node.id).lower()

    # 2. Check for branch root ID modification
    # Ensure selected_node.id is never assigned to a non-root child node
    for n in draft.nodes:
        if n.id != branch_root_id and n.id.lower() == selected_node_id_str:
            errors.append(
                f"Selected node ID {selected_node_id_str!r} cannot be assigned to non-root child node {n.id!r}"
            )

    # Check if the branch root node has an explicit ID (e.g. UUID) that doesn't match selected_node.id
    is_root_provisional = (
        bool(re.match(r"^n[0-9]+$", branch_root_id, re.IGNORECASE)) or branch_root_id.lower() == "root"
    )
    if not is_root_provisional and branch_root_id.lower() != selected_node_id_str:
        errors.append(f"Branch root ID cannot be modified (expected {selected_node_id_str!r}, got {branch_root_id!r})")

    # 3. Check for IDs outside the allowed branch
    if allowed_node_ids is not None:
        clean_allowed = {str(i).lower() for i in allowed_node_ids}
        for n in draft.nodes:
            is_prov = bool(re.match(r"^n[0-9]+$", n.id, re.IGNORECASE)) or n.id.lower() == "root"
            if not is_prov:
                try:
                    UUID(n.id)
                    is_uuid = True
                except ValueError:
                    is_uuid = False

                if is_uuid and n.id.lower() not in clean_allowed:
                    errors.append(f"Node ID {n.id!r} is outside the allowed branch (DR-03: branch boundary violation)")
                elif not is_uuid and n.id.lower() not in clean_allowed:
                    errors.append(f"Node ID {n.id!r} is outside the allowed branch")

    # 4. Check ancestor labels (read-only context enforcement)
    if ancestor_labels:
        ancestor_set = {a.strip().lower() for a in ancestor_labels if a and a.strip()}
        for n in draft.nodes:
            # Ancestors must never be included as generated/modified nodes in the branch
            if n.label.strip().lower() in ancestor_set:
                errors.append(
                    f"Ancestor label {n.label!r} cannot be included as a node in the regenerated branch (ancestors are read-only)"
                )

    if errors:
        raise ValidationFailure(errors)

    return draft


def resolve_branch_to_records(
    draft: DraftMindMap,
    *,
    selected_node: NodeDraft,
    allowed_node_ids: set[str] | None = None,
    supporting_context: list[Any] | None = None,
) -> tuple[list[NodeDraft], list[EdgeDraft]]:
    """Resolves branch draft nodes and edges to wire-contract records.

    The branch root is guaranteed to retain selected_node.id.
    Descendants with existing allowed UUIDs keep them, while newly generated
    nodes receive fresh UUIDs.
    """
    clean_allowed = {str(i).lower() for i in allowed_node_ids} if allowed_node_ids else set()

    # Find root
    incoming: dict[str, int] = {n.id: 0 for n in draft.nodes}
    for e in draft.edges:
        if e.target in incoming:
            incoming[e.target] += 1
    roots = [n_id for n_id, count in incoming.items() if count == 0]
    root_id = roots[0]

    id_map: dict[str, UUID] = {root_id: selected_node.id}

    for n in draft.nodes:
        if n.id == root_id:
            continue
        try:
            parsed_uuid = UUID(n.id)
            if str(parsed_uuid).lower() in clean_allowed:
                id_map[n.id] = parsed_uuid
            else:
                id_map[n.id] = uuid4()
        except ValueError:
            id_map[n.id] = uuid4()

    context_list = supporting_context or []

    def _resolve_sources(raw_sources: list[str]) -> list[str]:
        if not context_list:
            return raw_sources
        resolved: list[str] = []
        for s in raw_sources:
            match = re.match(r"^ctx:(\d+)$", s.strip())
            if match:
                idx = int(match.group(1)) - 1
                if 0 <= idx < len(context_list):
                    chunk = context_list[idx]
                    title = getattr(chunk.record, "source_title", None)
                    url = getattr(chunk.record, "source_url", None)
                    if title and url:
                        resolved.append(f"{title} ({url})")
                    elif url:
                        resolved.append(url)
                    elif title:
                        resolved.append(title)
                    else:
                        resolved.append(s)
                else:
                    resolved.append(s)
            else:
                resolved.append(s)
        return resolved

    nodes = [
        NodeDraft(
            id=id_map[n.id],
            label=n.label,
            description=n.description,
            node_type=n.node_type,
            background_color=n.background_color,
            text_color=n.text_color,
            sources=_resolve_sources(n.sources),
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
