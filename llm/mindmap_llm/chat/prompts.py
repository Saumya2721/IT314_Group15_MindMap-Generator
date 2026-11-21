"""
Prompt builders for the chat turn agent.

Separated from agent logic so prompts can be unit-tested and adjusted
without touching the agent control flow — the same pattern used by
orchestration/prompts.py for the generation pipeline.
"""

from __future__ import annotations

from contracts.models import ChatMessage, EdgeDraft, MapSnapshot, NodeDraft
from rag.pinecone_store import RetrievedChunkRecord

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialise_node(node: NodeDraft) -> str:
    desc = f" — {node.description}" if node.description else ""
    return f"  [node:{node.id}] {node.label}{desc}"


def _serialise_edge(edge: EdgeDraft, nodes_by_id: dict) -> str:
    src = nodes_by_id.get(edge.source_node_id, {}).get("label", str(edge.source_node_id))
    tgt = nodes_by_id.get(edge.target_node_id, {}).get("label", str(edge.target_node_id))
    rel = f" ({edge.label})" if edge.label else ""
    return f"  {src} → {tgt}{rel}"


def _map_summary(map_snapshot: MapSnapshot) -> str:
    nodes_by_id = {str(n.id): {"label": n.label} for n in map_snapshot.nodes}
    node_lines = [_serialise_node(n) for n in map_snapshot.nodes]
    edge_lines = [_serialise_edge(e, nodes_by_id) for e in map_snapshot.edges]
    parts: list[str] = []
    if node_lines:
        parts.append("NODES:\n" + "\n".join(node_lines))
    if edge_lines:
        parts.append("EDGES:\n" + "\n".join(edge_lines))
    return "\n\n".join(parts) if parts else "(empty map — no nodes yet)"


def _format_history(history: list[ChatMessage]) -> str:
    if not history:
        return ""
    lines = []
    for msg in history:
        prefix = "User" if msg.role == "user" else "Assistant"
        lines.append(f"{prefix}: {msg.content}")
    return "\n".join(lines)


def format_conversation_context(
    history: list[ChatMessage],
    summary: str | None = None,
) -> list[str]:
    """Format summary and recent messages into prompt sections."""
    sections: list[str] = []
    if summary and summary.strip():
        sections.append(f"## Historical Conversation Summary (Background Context Only)\n{summary.strip()}")
    history_block = _format_history(history)
    if history_block:
        header = "## Recent Conversation" if summary else "## Conversation History"
        sections.append(f"{header}\n\n{history_block}")
    return sections


def _format_chunks(chunks: list[RetrievedChunkRecord]) -> str:
    if not chunks:
        return "(no additional reference material retrieved)"
    parts = []
    for i, chunk in enumerate(chunks, 1):
        title = chunk.source_title or chunk.source or f"source-{i}"
        url = f" <{chunk.source_url}>" if chunk.source_url else ""
        parts.append(f"[{i}] {title}{url}\n{chunk.chunk_text}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# QA prompts
# ---------------------------------------------------------------------------


def build_chat_qa_system_prompt(map_snapshot: MapSnapshot) -> str:
    return (
        "You are a helpful assistant that answers questions about a mind map.\n\n"
        "## Current Mind Map\n\n"
        f"{_map_summary(map_snapshot)}\n\n"
        "## Instructions\n\n"
        "- Only state facts that appear in the map or reference material below.\n"
        "- Do NOT invent nodes, relationships, or facts that are not present.\n"
        "- When you refer to a node, ALWAYS write its human-readable title/label in bold (e.g. **Policy Gradient**) and append its tag [node:<uuid>] immediately after so the interface can link directly to it.\n"
        "- Never refer to a node using just a raw hexadecimal UUID in conversational prose without its title.\n"
        "- When you cite a reference passage, include its number, e.g. [1].\n"
        "- Keep answers concise and grounded.\n"
    )


def build_chat_qa_user_prompt(
    message: str,
    history: list[ChatMessage],
    chunks: list[RetrievedChunkRecord],
    summary: str | None = None,
) -> str:
    chunks_block = _format_chunks(chunks)
    parts = []
    parts.extend(format_conversation_context(history, summary))
    parts.append(f"## Reference Material\n\n{chunks_block}")
    parts.append(f"## User Question\n\n{message}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Small talk prompts
# ---------------------------------------------------------------------------


def build_small_talk_system_prompt() -> str:
    return (
        "You are a friendly assistant embedded inside a mind map editor. "
        "The user is making small talk or greeting you. "
        "Reply naturally and briefly. "
        "Do not mention map nodes, edges, or suggest any changes."
    )


def build_small_talk_user_prompt(
    message: str,
    history: list[ChatMessage],
    summary: str | None = None,
) -> str:
    sections = format_conversation_context(history, summary)
    if sections:
        return "\n\n".join(sections) + f"\n\nUser: {message}"
    return message


# ---------------------------------------------------------------------------
# Action proposal prompts
# ---------------------------------------------------------------------------


def build_proposal_system_prompt(map_snapshot: MapSnapshot) -> str:
    node_list = "\n".join(f"  {n.id}  {n.label}" for n in map_snapshot.nodes)
    if not node_list:
        node_list = "  (no nodes yet)"
    return (
        "You are a mind map editing assistant. "
        "The user wants to modify their mind map. "
        "Your job is to understand the request and return a JSON action proposal.\n\n"
        "## Available Nodes\n\n"
        f"{node_list}\n\n"
        "## Output format\n\n"
        "Return ONLY valid JSON with these fields:\n"
        "{\n"
        '  "action_type": "<edit_proposal|regen_whole|regen_node>",\n'
        '  "target_node_ids": ["<uuid>", ...],\n'
        '  "parameters": {\n'
        '    "update_nodes": [{"id": "<uuid>", "label": "New label", "description": "optional"}],\n'
        '    "create_nodes": [{"id": "<new-uuid>", "label": "New node", "description": "optional"}],\n'
        '    "delete_nodes": ["<uuid-to-delete>"],\n'
        '    "update_edges": [{"id": "<edge-uuid>", "label": "updated relation"}],\n'
        '    "create_edges": [{"source_node_id": "<parent-uuid>", "target_node_id": "<child-uuid>", "label": "relation"}],\n'
        '    "delete_edges": ["<edge-uuid-to-delete>"]\n'
        "  },\n"
        '  "explanation": "<human-readable description of the proposed change>",\n'
        '  "instruction": "<instruction string if regen_whole or regen_node, else null>",\n'
        '  "needs_clarification": false,\n'
        '  "clarification_question": null\n'
        "}\n\n"
        "Rules:\n"
        "- When action_type is 'edit_proposal': 'parameters' MUST contain one or more delta arrays (update_nodes, create_nodes, delete_nodes, create_edges, delete_edges).\n"
        "- For create_nodes: assign an id (UUID or provisional key like 'new-1') so create_edges can link to it.\n"
        "- For create_edges: source_node_id and target_node_id are required; edge id is optional and assigned by the server if omitted.\n"
        "- When action_type is 'regen_node': target_node_ids MUST contain exactly the one node to regenerate, 'instruction' describes the focus, and 'parameters' is {}.\n"
        "- When action_type is 'regen_whole': 'instruction' describes the regeneration, target_node_ids is empty, and 'parameters' is {}.\n"
        "- Existing node IDs in target_node_ids, update_nodes, delete_nodes, and edge endpoints MUST belong to the Available Nodes list above.\n"
        "- If the user's request is ambiguous, set needs_clarification=true and provide a clarification_question.\n"
        "- If you cannot determine which node is meant, set needs_clarification=true.\n"
        "- Do not output any text outside the JSON object.\n"
    )


def build_proposal_user_prompt(
    message: str,
    history: list[ChatMessage],
    summary: str | None = None,
) -> str:
    sections = format_conversation_context(history, summary)
    parts = []
    if sections:
        parts.extend(sections)
    parts.append(f"## User request\n\n{message}")
    return "\n\n".join(parts)
