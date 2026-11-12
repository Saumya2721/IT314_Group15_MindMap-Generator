"""Graph validation module for Mind Map Generator.

Implements domain requirements:
  DR-01: Graph-based representation (nodes and directed edges)
  DR-02: Single root concept (exactly one central node from which all concepts branch)
  DR-03: Unique node identification
  DR-04: Relationship information & same-map integrity
  No cycles: Enforces strict acyclic directed hierarchy
  No self-loops: An edge cannot start and end at the same node
"""

from __future__ import annotations

import re
from collections import deque
from typing import Any
from uuid import UUID

HEX_COLOR_REGEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


class GraphValidationError(Exception):
    """Raised when a mind map graph fails topological or domain validation."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def _normalize_uuid(val: Any) -> UUID | None:
    if val is None:
        return None
    if isinstance(val, UUID):
        return val
    try:
        return UUID(str(val))
    except (ValueError, TypeError):
        return None


def validate_graph_structure(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    map_id: UUID | None = None,
) -> list[str]:
    """Validate mind map graph structure against all domain requirements.

    Returns a list of error strings. If valid, the list is empty.
    """
    errors: list[str] = []

    # 1. Unique Node Identifiers (DR-03) & Node Field Validation
    seen_node_ids: set[UUID] = set()

    for idx, node in enumerate(nodes):
        raw_id = node.get("id")
        norm_id = _normalize_uuid(raw_id)
        if not norm_id:
            errors.append(
                f"Node at index {idx} has missing or invalid UUID 'id': {raw_id!r}"
            )
            continue

        if norm_id in seen_node_ids:
            errors.append(
                f"Duplicate node id '{norm_id}' (violates DR-03: unique node identification)"
            )
        else:
            seen_node_ids.add(norm_id)

        label = node.get("label")
        if not label or not str(label).strip():
            errors.append(f"Node '{norm_id}' must have a non-empty label")
        elif len(str(label)) > 255:
            errors.append(
                f"Node '{norm_id}' label exceeds maximum length of 255 characters"
            )

        bg_color = node.get("background_color")
        if bg_color and not HEX_COLOR_REGEX.match(str(bg_color)):
            errors.append(
                f"Node '{norm_id}' has invalid background_color '{bg_color}'. Must be #RRGGBB hex format."
            )

        text_color = node.get("text_color")
        if text_color and not HEX_COLOR_REGEX.match(str(text_color)):
            errors.append(
                f"Node '{norm_id}' has invalid text_color '{text_color}'. Must be #RRGGBB hex format."
            )

        width = node.get("width")
        if width is not None:
            try:
                if float(width) <= 0:
                    errors.append(f"Node '{norm_id}' width must be greater than 0")
            except (ValueError, TypeError):
                errors.append(f"Node '{norm_id}' width must be a valid number")

        height = node.get("height")
        if height is not None:
            try:
                if float(height) <= 0:
                    errors.append(f"Node '{norm_id}' height must be greater than 0")
            except (ValueError, TypeError):
                errors.append(f"Node '{norm_id}' height must be a valid number")

        if map_id is not None:
            node_map_id = _normalize_uuid(node.get("map_id"))
            if node_map_id is not None and node_map_id != map_id:
                errors.append(
                    f"Node '{norm_id}' belongs to map '{node_map_id}', not target map '{map_id}'"
                )

    if not nodes:
        if edges:
            errors.append("Cannot have edges in an empty mind map with 0 nodes")
        return errors

    seen_edges: set[tuple[UUID, UUID, str | None]] = set()
    normalized_edges: list[tuple[UUID, UUID]] = []

    for idx, edge in enumerate(edges):
        raw_source = edge.get("source_node_id") or edge.get("source")
        raw_target = edge.get("target_node_id") or edge.get("target")

        source_id = _normalize_uuid(raw_source)
        target_id = _normalize_uuid(raw_target)

        if not source_id:
            errors.append(f"Edge at index {idx} has invalid source node id: {raw_source!r}")
            continue
        if not target_id:
            errors.append(f"Edge at index {idx} has invalid target node id: {raw_target!r}")
            continue

        if source_id not in seen_node_ids:
            errors.append(f"Edge references unknown source node '{source_id}'")
        if target_id not in seen_node_ids:
            errors.append(f"Edge references unknown target node '{target_id}'")

        if source_id == target_id:
            errors.append(
                f"Self-loop detected on node '{source_id}' (an edge cannot connect a node to itself)"
            )

        edge_label = edge.get("label")
        edge_key = (source_id, target_id, edge_label)
        if edge_key in seen_edges:
            errors.append(
                f"Duplicate edge detected between source '{source_id}' and target '{target_id}' with label {edge_label!r}"
            )
        else:
            seen_edges.add(edge_key)

        if map_id is not None:
            edge_map_id = _normalize_uuid(edge.get("map_id"))
            if edge_map_id is not None and edge_map_id != map_id:
                errors.append(
                    f"Edge '{edge.get('id')}' belongs to map '{edge_map_id}', not target map '{map_id}'"
                )

        if source_id in seen_node_ids and target_id in seen_node_ids and source_id != target_id:
            normalized_edges.append((source_id, target_id))

    if errors:
        return errors

    in_degrees: dict[UUID, int] = {nid: 0 for nid in seen_node_ids}
    adjacency: dict[UUID, list[UUID]] = {nid: [] for nid in seen_node_ids}

    for src, tgt in normalized_edges:
        in_degrees[tgt] = in_degrees.get(tgt, 0) + 1
        adjacency[src].append(tgt)

    candidate_roots = [nid for nid, in_deg in in_degrees.items() if in_deg == 0]

    if len(candidate_roots) == 0:
        errors.append(
            "No root node found — every node has an incoming edge (graph has a cycle with no entry point)."
        )
        return errors

    if len(candidate_roots) > 1:
        root_strs = [str(r) for r in candidate_roots[:5]]
        errors.append(
            f"Found {len(candidate_roots)} root concepts {root_strs} — "
            f"DR-02 requires exactly one central root node in a mind map."
        )
        return errors

    root_id = candidate_roots[0]

    kahn_in_degrees = dict(in_degrees)
    queue: deque[UUID] = deque([root_id])
    visited_count = 0

    while queue:
        curr = queue.popleft()
        visited_count += 1
        for neighbor in adjacency.get(curr, []):
            kahn_in_degrees[neighbor] -= 1
            if kahn_in_degrees[neighbor] == 0:
                queue.append(neighbor)

    if visited_count != len(seen_node_ids):
        errors.append(
            "Cycle detected in mind map graph. Relationships must form an acyclic hierarchy without circular paths."
        )
        return errors

    reachable: set[UUID] = set()
    bfs_queue: deque[UUID] = deque([root_id])

    while bfs_queue:
        curr = bfs_queue.popleft()
        if curr in reachable:
            continue
        reachable.add(curr)
        for child in adjacency.get(curr, []):
            if child not in reachable:
                bfs_queue.append(child)

    unreachable = seen_node_ids - reachable
    if unreachable:
        unreachable_strs = sorted(str(u) for u in list(unreachable)[:5])
        errors.append(
            f"Disconnected nodes detected {unreachable_strs}. All nodes in the mind map must branch "
            f"and be reachable from the central root node '{root_id}'."
        )

    return errors


def check_edge_creates_cycle(
    existing_edges: list[dict[str, Any]],
    new_source_id: UUID,
    new_target_id: UUID,
) -> bool:
    """Check if adding directed edge new_source_id -> new_target_id would create a cycle.

    A cycle will be created if and only if new_source_id == new_target_id (self-loop)
    OR there is already an existing directed path from new_target_id to new_source_id.
    """
    if new_source_id == new_target_id:
        return True

    adj: dict[UUID, list[UUID]] = {}
    for edge in existing_edges:
        raw_src = edge.get("source_node_id") or edge.get("source")
        raw_tgt = edge.get("target_node_id") or edge.get("target")
        s = _normalize_uuid(raw_src)
        t = _normalize_uuid(raw_tgt)
        if s and t:
            adj.setdefault(s, []).append(t)

    queue: deque[UUID] = deque([new_target_id])
    visited: set[UUID] = set()

    while queue:
        curr = queue.popleft()
        if curr == new_source_id:
            return True
        if curr in visited:
            continue
        visited.add(curr)
        for neighbor in adj.get(curr, []):
            if neighbor not in visited:
                queue.append(neighbor)

    return False


def find_root_node_id(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> UUID | None:
    """Return the UUID of the central root node if a valid single root exists, else None."""
    node_ids: set[UUID] = set()
    for n in nodes:
        uid = _normalize_uuid(n.get("id"))
        if uid:
            node_ids.add(uid)

    if not node_ids:
        return None

    incoming: dict[UUID, int] = {nid: 0 for nid in node_ids}
    for e in edges:
        tgt = _normalize_uuid(e.get("target_node_id") or e.get("target"))
        if tgt and tgt in incoming:
            incoming[tgt] += 1

    roots = [nid for nid, count in incoming.items() if count == 0]
    return roots[0] if len(roots) == 1 else None


def get_node_descendant_ids(
    edges: list[dict[str, Any]],
    root_node_id: UUID,
) -> set[UUID]:
    """Find all descendant node IDs downstream of root_node_id."""
    adj: dict[UUID, list[UUID]] = {}
    for edge in edges:
        raw_src = edge.get("source_node_id") or edge.get("source")
        raw_tgt = edge.get("target_node_id") or edge.get("target")
        s = _normalize_uuid(raw_src)
        t = _normalize_uuid(raw_tgt)
        if s and t:
            adj.setdefault(s, []).append(t)

    descendants: set[UUID] = set()
    queue: deque[UUID] = deque(adj.get(root_node_id, []))

    while queue:
        curr = queue.popleft()
        if curr in descendants:
            continue
        descendants.add(curr)
        for child in adj.get(curr, []):
            if child not in descendants:
                queue.append(child)

    return descendants
