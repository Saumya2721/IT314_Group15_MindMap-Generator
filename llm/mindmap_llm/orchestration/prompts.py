from __future__ import annotations

import json
import re
from typing import Any

GENERATION_SYSTEM_PROMPT = """\
You are the mind-map generation engine for a mind-mapping tool. Given a \
seed (raw ideas, uploaded material, or both) and optional supporting \
context, produce a structured mind map as nodes and edges.

Rules (do not violate these):
- Output ONLY a single JSON object, no prose before or after it, no markdown code fences.
- The JSON object has exactly two keys: "nodes" and "edges".
- Each node: {"id": "<short unique string id, e.g. n1>", "label": "<short concept, <= 6 words>", \
"description": "<1-3 sentence explanation, or null>", "node_type": "default"}
- Each edge: {"source": "<node id>", "target": "<node id>", "label": "<short relationship, e.g. 'causes', 'is-a', 'contains', or null>"}
- There must be EXACTLY ONE root node with no incoming edges — the central concept — and every \
other node must be reachable from it. Do not create disconnected clusters.
- Every node id referenced by an edge must exist in "nodes".
- No self-loop edges (a node's id can't be both source and target of the same edge).
- Preserve the original meaning of the seed; do not invent facts not grounded in the seed or the \
supplied supporting context.
- Detail level "overview": 5-10 nodes, high-level only. "balanced": 10-25 nodes, one level of \
sub-detail. "detailed": 25-60 nodes, multiple levels of sub-branches.
"""  # noqa: E501


def build_generation_user_prompt(
    *,
    seed_text: str,
    detail_level: str,
    supporting_context: list[str],
    regeneration_instruction: str | None,
    previous_validation_errors: list[str] | None,
) -> str:
    parts = [f"Detail level: {detail_level}", "", "Seed:", seed_text.strip()]

    if regeneration_instruction:
        parts += [
            "",
            "This is a REGENERATION of an existing map. The seed above is the original, "
            "unchanged base context. Apply this additional instruction on top of it:",
            regeneration_instruction.strip(),
        ]

    if supporting_context:
        parts += ["", "Supporting context retrieved for grounding (use only what's relevant):"]
        for i, chunk in enumerate(supporting_context, 1):
            parts.append(f"[{i}] {chunk.strip()}")

    if previous_validation_errors:
        parts += [
            "",
            "Your previous attempt was rejected for these reasons — fix ALL of them this time:",
            *[f"- {e}" for e in previous_validation_errors],
        ]

    parts += ["", "Respond with ONLY the JSON object described in the system prompt."]
    return "\n".join(parts)


class JSONExtractionError(Exception):
    pass


def extract_json_object(text: str | None) -> dict[str, Any]:
    """The LLM is instructed to return raw JSON, but models sometimes wrap
    it in a code fence anyway — strip that defensively before parsing."""
    if text is None:
        raise JSONExtractionError("Model returned no text")
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise JSONExtractionError("No JSON object found in model output")
    try:
        result: dict[str, Any] = json.loads(match.group(0))
        return result
    except json.JSONDecodeError as exc:
        raise JSONExtractionError(f"Model output was not valid JSON: {exc}") from exc
