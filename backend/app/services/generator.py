"""Mind Map Generator module.

Bridges the backend in-process generation service directly to the LLM
orchestration prompts, provider abstraction, and validation schema.
"""

# ruff: noqa: E402
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

# Ensure llm and mindmap_llm directories are in sys.path for prompt and validation helpers
for d in (
    Path(__file__).resolve().parent.parent.parent.parent / "llm" / "mindmap_llm",
    Path(__file__).resolve().parent.parent.parent.parent / "llm",
    Path(__file__).resolve().parent.parent.parent / "llm" / "mindmap_llm",
    Path(__file__).resolve().parent.parent.parent / "llm",
):
    if d.exists() and str(d) not in sys.path:
        sys.path.append(str(d))

try:
    from orchestration.prompts import (
        GENERATION_SYSTEM_PROMPT,
        JSONExtractionError,
        build_generation_user_prompt,
        extract_json_object,
    )
    from providers.base import LLMProvider, Message
    from validation.schema import ValidationFailure, validate_draft
except ImportError:
    try:
        from mindmap_llm.orchestration.prompts import (
            GENERATION_SYSTEM_PROMPT,
            JSONExtractionError,
            build_generation_user_prompt,
            extract_json_object,
        )
        from mindmap_llm.providers.base import LLMProvider, Message
        from mindmap_llm.validation.schema import ValidationFailure, validate_draft
    except ImportError:
        GENERATION_SYSTEM_PROMPT = ""
        JSONExtractionError = Exception

        def _fallback_build_generation_user_prompt(
            seed_text: str,
            detail_level: str = "overview",
            supporting_context: list[str] | None = None,
            regeneration_instruction: str | None = None,
            previous_validation_errors: list[str] | None = None,
        ) -> str:
            parts = [f"Topic / Ideas:\n{seed_text}", f"Detail Level: {detail_level}"]
            if supporting_context:
                parts.append("Context:\n" + "\n".join(supporting_context))
            if regeneration_instruction:
                parts.append(f"Instruction:\n{regeneration_instruction}")
            if previous_validation_errors:
                parts.append("Errors to fix:\n" + "\n".join(previous_validation_errors))
            return "\n\n".join(parts)

        build_generation_user_prompt = _fallback_build_generation_user_prompt

        def _fallback_extract_json_object(text: str) -> dict[str, Any]:
            import json
            import re

            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            raise JSONExtractionError("No JSON object found")

        extract_json_object = _fallback_extract_json_object
        LLMProvider = Any
        Message = Any
        ValidationFailure = Exception

        def _fallback_validate_draft(draft: dict[str, Any]) -> dict[str, Any]:
            return draft

        validate_draft = _fallback_validate_draft

logger = logging.getLogger(__name__)

# Visual layout constants for deterministic Top-to-Bottom (TB) tree placement
_ROOT_Y = 80.0
_ROW_HEIGHT = 200.0
_CARD_WIDTH = 260.0
_SIBLING_GAP_X = 40.0
_COMPONENT_GAP_X = 120.0

# Legacy aliases for backwards compatibility
_ROOT_X = 80.0
_COLUMN_WIDTH = _CARD_WIDTH
_NODE_HEIGHT = _ROW_HEIGHT
_SIBLING_GAP = _SIBLING_GAP_X
_COMPONENT_GAP = _COMPONENT_GAP_X


def _has_valid_coords(n: dict[str, Any]) -> bool:
    """Return True if node already has distinct non-zero coordinates."""
    try:
        px = float(n.get("pos_x", 0.0))
        py = float(n.get("pos_y", 0.0))
        return px != 0.0 and py != 0.0
    except (ValueError, TypeError):
        return False


def compute_layout(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    """Calculate 2D coordinates (pos_x, pos_y) for nodes arranged in a clean mind map layout.

    Positions nodes in a deterministic Top-to-Bottom (TB) tree layout:
    Root at apex (y=80), branches horizontally distributed at level 1 (y=280),
    and concepts/leaves further down (y=480+), centering parents over child spans,
    guaranteeing zero card collisions, and preserving manually dragged coordinates.
    """
    if not nodes:
        return

    # If all nodes already have non-zero coordinates, preserve stored layout
    if len(nodes) > 1 and all(_has_valid_coords(n) for n in nodes):
        return

    node_by_id: dict[str, dict[str, Any]] = {str(n["id"]): n for n in nodes}

    # Build adjacency lists supporting both source/target and source_node_id/target_node_id
    children_map: dict[str, list[str]] = {nid: [] for nid in node_by_id}
    parents_map: dict[str, list[str]] = {nid: [] for nid in node_by_id}
    in_degree: dict[str, int] = {nid: 0 for nid in node_by_id}

    for e in edges:
        src = str(e.get("source") or e.get("source_node_id") or "")
        tgt = str(e.get("target") or e.get("target_node_id") or "")
        if src in children_map and tgt in node_by_id:
            children_map[src].append(tgt)
            parents_map[tgt].append(src)
            in_degree[tgt] += 1

    # Identify primary spanning forest to break multi-parent cross-links cleanly
    visited: set[str] = set()
    tree_children: dict[str, list[str]] = {nid: [] for nid in node_by_id}
    depth_map: dict[str, int] = {}
    component_roots: list[str] = []

    # 1. First candidate: explicit root node or zero in-degree nodes
    explicit_roots = [nid for nid, n in node_by_id.items() if n.get("node_type") == "root"]
    zero_in_degree = [nid for nid, deg in in_degree.items() if deg == 0 and nid not in explicit_roots]
    ordered_candidates = explicit_roots + zero_in_degree

    if not ordered_candidates:
        ordered_candidates = [str(nodes[0]["id"])]

    for cand_id in ordered_candidates:
        if cand_id in visited:
            continue
        component_roots.append(cand_id)
        visited.add(cand_id)
        depth_map[cand_id] = 0
        queue = [cand_id]
        while queue:
            curr = queue.pop(0)
            curr_depth = depth_map[curr]
            for child_id in children_map[curr]:
                if child_id not in visited:
                    visited.add(child_id)
                    tree_children[curr].append(child_id)
                    depth_map[child_id] = curr_depth + 1
                    queue.append(child_id)

    # 2. Cover any disconnected components or cycles not reached above
    for n in nodes:
        nid = str(n["id"])
        if nid not in visited:
            component_roots.append(nid)
            visited.add(nid)
            depth_map[nid] = 0
            queue = [nid]
            while queue:
                curr = queue.pop(0)
                curr_depth = depth_map[curr]
                for child_id in children_map[curr]:
                    if child_id not in visited:
                        visited.add(child_id)
                        tree_children[curr].append(child_id)
                        depth_map[child_id] = curr_depth + 1
                        queue.append(child_id)

    # 3. Calculate subtree widths bottom-up
    subtree_width: dict[str, float] = {}

    def calc_subtree_width(u: str) -> float:
        children = tree_children[u]
        if not children:
            subtree_width[u] = _CARD_WIDTH
            return _CARD_WIDTH
        total_children_w = sum(calc_subtree_width(c) for c in children)
        gap_total = (len(children) - 1) * _SIBLING_GAP_X
        w = max(_CARD_WIDTH, total_children_w + gap_total)
        subtree_width[u] = w
        return w

    # 4. Top-down assignment of horizontal slices and vertical depth rows
    def assign_positions(u: str, x_left: float, x_right: float):
        node = node_by_id[u]
        center_x = (x_left + x_right) / 2.0
        # If node already has user-dragged coordinates and is not at (0, 0), preserve it
        if not _has_valid_coords(node):
            node["pos_x"] = round(center_x, 1)
            node["pos_y"] = round(_ROOT_Y + depth_map[u] * _ROW_HEIGHT, 1)

        children = tree_children[u]
        if not children:
            return

        children_total_w = sum(subtree_width[c] for c in children) + (len(children) - 1) * _SIBLING_GAP_X
        child_cursor = center_x - (children_total_w / 2.0)

        for c in children:
            c_w = subtree_width[c]
            assign_positions(c, child_cursor, child_cursor + c_w)
            child_cursor += c_w + _SIBLING_GAP_X

    current_x_cursor = 80.0
    for r in component_roots:
        calc_subtree_width(r)
        comp_w = subtree_width[r]
        assign_positions(r, current_x_cursor, current_x_cursor + comp_w)
        current_x_cursor += comp_w + _COMPONENT_GAP_X


def compute_incremental_layout(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    new_node_ids: set[str] | list[str],
) -> None:
    """Position newly added nodes without shifting existing stable nodes.

    Places new children relative to their parent anchor and to the right of any
    existing siblings, avoiding bounding-box intersections with established cards.
    """
    if not new_node_ids:
        return

    # Preserve deterministic ordering for list/tuple or sort for sets
    ordered_new_ids = list(dict.fromkeys(str(nid) for nid in new_node_ids))
    new_id_set = set(ordered_new_ids)
    node_by_id = {str(n["id"]): n for n in nodes}

    # Build parent relationships for all nodes in edges
    parent_map: dict[str, str] = {}
    for e in edges:
        src = str(e.get("source") or e.get("source_node_id") or "")
        tgt = str(e.get("target") or e.get("target_node_id") or "")
        if src in node_by_id and tgt in node_by_id:
            parent_map[tgt] = src

    for nid in ordered_new_ids:
        node = node_by_id.get(nid)
        if not node:
            continue

        parent_id = parent_map.get(nid)
        parent_node = node_by_id.get(parent_id) if parent_id else None

        if parent_node and _has_valid_coords(parent_node):
            target_y = float(parent_node["pos_y"]) + _ROW_HEIGHT
            # Find siblings of this new node that already have coordinates
            sibling_xs = [
                float(n["pos_x"])
                for s_id, n in node_by_id.items()
                if s_id != nid and parent_map.get(s_id) == parent_id and _has_valid_coords(n)
            ]
            target_x = max(sibling_xs) + _CARD_WIDTH + _SIBLING_GAP_X if sibling_xs else float(parent_node["pos_x"])

            # Check for collision against any existing placed nodes at target (x, y)
            collision = True
            while collision:
                collision = any(
                    abs(float(other.get("pos_y", 0.0)) - target_y) < (_ROW_HEIGHT * 0.7)
                    and abs(float(other.get("pos_x", 0.0)) - target_x) < _CARD_WIDTH
                    for other_id, other in node_by_id.items()
                    if other_id != nid and _has_valid_coords(other)
                )
                if collision:
                    target_x += _CARD_WIDTH + _SIBLING_GAP_X

            node["pos_x"] = round(target_x, 1)
            node["pos_y"] = round(target_y, 1)
        else:
            # Fallback for orphaned additions: place to the right of current occupied bounds
            max_x = max(
                (float(n.get("pos_x", 80.0)) for n_id, n in node_by_id.items() if n_id not in new_id_set),
                default=80.0,
            )
            node["pos_x"] = round(max_x + _CARD_WIDTH + _SIBLING_GAP_X, 1)
            node["pos_y"] = round(_ROOT_Y + _ROW_HEIGHT, 1)


class MindMapGenerator:
    """Decoupled Mind Map Generator.

    Orchestrates prompting, provider invocation, JSON parsing, validation,
    and automatic graph layout.
    """

    def __init__(
        self,
        llm: LLMProvider,
        max_tokens: int = 8000,
        max_retries: int = 2,
    ):
        self.llm = llm
        self.max_tokens = max_tokens
        self.max_retries = max_retries

    async def generate(
        self,
        *,
        topic: str | None = None,
        ideas: str | None = None,
        detail_level: str | None = "overview",
        context: str | None = None,
        regeneration_instruction: str | None = None,
    ) -> dict[str, Any]:
        """Generate a structured mind map draft conforming to DR-01 - DR-04."""
        seed_parts = [p.strip() for p in (topic, ideas, context) if p and p.strip()]
        seed_text = "\n\n".join(seed_parts) if seed_parts else (topic or "Mind Map")

        supporting_context = [context] if context else []
        validation_errors: list[str] = []

        last_raw_response = ""

        for attempt in range(self.max_retries + 1):
            prompt = build_generation_user_prompt(
                seed_text=seed_text,
                detail_level=detail_level or "overview",
                supporting_context=supporting_context,
                regeneration_instruction=regeneration_instruction,
                previous_validation_errors=validation_errors if validation_errors else None,
            )

            logger.info(
                "MindMapGenerator invoking LLM (attempt %d/%d)...",
                attempt + 1,
                self.max_retries + 1,
            )
            response = await self.llm.generate(
                [Message(role="user", content=prompt)],
                system=GENERATION_SYSTEM_PROMPT,
                max_tokens=self.max_tokens,
                temperature=0.3,
            )
            last_raw_response = response.text

            try:
                payload = extract_json_object(response.text)
            except JSONExtractionError as exc:
                logger.warning("JSON extraction failed on attempt %d: %s", attempt + 1, exc)
                validation_errors = [f"Invalid JSON returned: {exc}"]
                continue

            try:
                draft = validate_draft(payload)
                # Validation passed! Convert draft to dict
                nodes = [
                    {
                        "id": n.id,
                        "label": n.label,
                        "description": n.description,
                        "node_type": n.node_type,
                        "background_color": n.background_color or ("#4F46E5" if n.node_type == "root" else "#3B82F6"),
                        "text_color": n.text_color or "#FFFFFF",
                        "pos_x": getattr(n, "pos_x", 0.0),
                        "pos_y": getattr(n, "pos_y", 0.0),
                    }
                    for n in draft.nodes
                ]
                edges = [
                    {
                        "source": e.source,
                        "target": e.target,
                        "label": e.label,
                    }
                    for e in draft.edges
                ]

                # Compute visually distinct coordinates for frontend display
                compute_layout(nodes, edges)

                return {"nodes": nodes, "edges": edges}

            except ValidationFailure as exc:
                logger.warning("Schema validation failed on attempt %d: %s", attempt + 1, exc)
                validation_errors = exc.errors

        # If retries exhausted, raise with informative diagnostics
        raise RuntimeError(
            f"MindMapGenerator failed to produce a valid schema after "
            f"{self.max_retries + 1} attempts. "
            f"Last validation errors: {validation_errors}. "
            f"LLM response was: {last_raw_response[:300]}..."
        )
