"""Mind Map Generator module.

Bridges the backend in-process generation service directly to the LLM
orchestration prompts, provider abstraction, and validation schema.
"""
# ruff: noqa: E402
from __future__ import annotations

import logging
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure llm directory is in sys.path for prompt and validation helpers
llm_dir = Path(__file__).resolve().parent.parent.parent / "llm"
if llm_dir.exists() and str(llm_dir) not in sys.path:
    sys.path.insert(0, str(llm_dir))

from orchestration.prompts import (
    GENERATION_SYSTEM_PROMPT,
    JSONExtractionError,
    build_generation_user_prompt,
    extract_json_object,
)
from providers.base import LLMProvider, Message
from validation.schema import ValidationFailure, validate_draft

logger = logging.getLogger(__name__)


def compute_layout(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> None:
    """Calculate 2D coordinates (pos_x, pos_y) for nodes arranged in a clean mind map layout.

    Places the central root node in the center (450, 300) and radiates children outward.
    """
    if not nodes:
        return

    # Check if all nodes already have non-zero distinct positions
    has_custom_positions = any(
        float(n.get("pos_x", 0.0)) != 0.0 or float(n.get("pos_y", 0.0)) != 0.0 for n in nodes
    )
    if has_custom_positions and len(nodes) > 1:
        return

    # Build adjacency list supporting both source/target and source_node_id/target_node_id
    children_map: Dict[str, List[str]] = {str(n["id"]): [] for n in nodes}
    in_degree: Dict[str, int] = {str(n["id"]): 0 for n in nodes}
    for e in edges:
        src = str(e.get("source") or e.get("source_node_id") or "")
        tgt = str(e.get("target") or e.get("target_node_id") or "")
        if src in children_map and tgt in in_degree:
            children_map[src].append(tgt)
            in_degree[tgt] += 1

    # Find candidate root
    root_id = None
    for n in nodes:
        nid = str(n["id"])
        if n.get("node_type") == "root" or in_degree.get(nid, 0) == 0:
            root_id = nid
            break
    if not root_id:
        root_id = str(nodes[0]["id"])

    node_by_id = {str(n["id"]): n for n in nodes}

    # Center origin
    center_x = 450.0
    center_y = 300.0

    root_node = node_by_id[root_id]
    root_node["pos_x"] = center_x
    root_node["pos_y"] = center_y
    root_node["node_type"] = "root"

    # Radial tree distribution
    level_1_children = children_map.get(root_id, [])
    l1_count = len(level_1_children)
    l1_radius = 240.0

    visited = {root_id}

    for i, child_id in enumerate(level_1_children):
        if child_id in visited:
            continue
        visited.add(child_id)
        angle = (2 * math.pi * i) / max(l1_count, 1)
        cx = center_x + l1_radius * math.cos(angle)
        cy = center_y + l1_radius * math.sin(angle)
        child_node = node_by_id[child_id]
        child_node["pos_x"] = round(cx, 1)
        child_node["pos_y"] = round(cy, 1)

        # Distribute level 2 grandchildren
        l2_children = [cid for cid in children_map.get(child_id, []) if cid not in visited]
        l2_count = len(l2_children)
        l2_radius = 160.0

        for j, gchild_id in enumerate(l2_children):
            visited.add(gchild_id)
            spread = 0.6  # radians
            offset_angle = angle - (spread / 2) + (spread * (j + 0.5) / max(l2_count, 1))
            gx = cx + l2_radius * math.cos(offset_angle)
            gy = cy + l2_radius * math.sin(offset_angle)
            gc_node = node_by_id[gchild_id]
            gc_node["pos_x"] = round(gx, 1)
            gc_node["pos_y"] = round(gy, 1)

    # For any remaining disconnected or deep nodes, arrange in a gentle grid/offset
    curr_offset = 50.0
    for n in nodes:
        nid = str(n["id"])
        if nid not in visited:
            n["pos_x"] = round(center_x + curr_offset, 1)
            n["pos_y"] = round(center_y + curr_offset, 1)
            curr_offset += 60.0


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
        topic: Optional[str] = None,
        ideas: Optional[str] = None,
        detail_level: Optional[str] = "overview",
        context: Optional[str] = None,
        regeneration_instruction: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate a structured mind map draft conforming to DR-01 - DR-04."""
        seed_parts = [p.strip() for p in (topic, ideas, context) if p and p.strip()]
        seed_text = "\n\n".join(seed_parts) if seed_parts else (topic or "Mind Map")

        supporting_context = [context] if context else []
        validation_errors: List[str] = []

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
                        "background_color": n.background_color
                        or ("#4F46E5" if n.node_type == "root" else "#3B82F6"),
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
