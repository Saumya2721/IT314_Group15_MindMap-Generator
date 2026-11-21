"""
Asynchronous User Memory Extraction and Proposal Engine (S3-L3).

Analyzes user interactions (chat turns and mind map generations) using an LLM turn,
extracts durable preferences, decisions, and domain facts, deduplicates and scores them,
and dispatches proposals to the backend via POST /internal/memory/propose asynchronously.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from uuid import UUID

from memory.client import MemoryClient
from memory.schemas import ProposedMemory, UserMemory
from memory.scoring import deduplicate_and_filter_proposals, is_transient_or_noise
from orchestration.prompts import JSONExtractionError, extract_json_object
from providers.base import LLMProvider, Message

logger = logging.getLogger(__name__)

_MAX_EXTRACTION_OUTPUT_TOKENS = 512


def build_memory_extraction_system_prompt() -> str:
    """Build system prompt directing the LLM to identify durable user memories."""
    return (
        "You are a user memory extractor for an AI mind mapping platform.\n"
        "Your task is to analyze user requests, interactions, and generated mind map concepts, "
        "and extract ONLY long-term, durable user preferences, facts, and decisions.\n\n"
        "DURABLE MEMORIES TO EXTRACT:\n"
        "- Explicit user preferences (e.g. 'User prefers concise technical definitions with real-world examples')\n"
        "- Stated domain expertise or context (e.g. 'User is researching deep learning architectures')\n"
        "- Key structural decisions (e.g. 'User prefers 3-level hierarchical mind maps with numbered steps')\n"
        "- Language, tone, or style preferences\n\n"
        "DO NOT EXTRACT:\n"
        "- Temporary UI instructions (e.g. 'zoom in', 'undo', 'change color to blue', 'delete node 3')\n"
        "- Small talk, greetings, or ephemeral questions (e.g. 'hi', 'how are you')\n"
        "- General knowledge facts from the topic that are NOT about the user\n"
        "- One-off specific edits that have no lasting utility across other maps\n\n"
        "OUTPUT FORMAT:\n"
        "Return ONLY a valid JSON object matching this schema:\n"
        "{\n"
        '  "memories": [\n'
        "    {\n"
        '      "content": "concise description of the preference or fact",\n'
        '      "memory_type": "preference | decision | fact | convention",\n'
        '      "importance_score": 0.5 to 1.0,\n'
        '      "is_durable": true\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "If there are no durable memories to extract, return:\n"
        '{"memories": []}'
    )


def build_memory_extraction_user_prompt(
    interaction_summary: str,
    user_inputs: list[str],
    existing_memories: list[str] | None = None,
) -> str:
    """Build the extraction user prompt."""
    lines: list[str] = ["## Current Interaction"]
    for idx, u_in in enumerate(user_inputs, start=1):
        lines.append(f"User Input {idx}: {u_in.strip()}")

    if interaction_summary:
        lines.append(f"\nAssistant/System Action Summary:\n{interaction_summary.strip()}")

    if existing_memories:
        lines.append("\n## Already Known Memories (DO NOT DUPLICATE)")
        for m in existing_memories[:10]:
            lines.append(f"- {m}")

    lines.append(
        "\nAnalyze the interaction and output any newly learned durable user memories in the specified JSON format."
    )
    return "\n".join(lines)


async def extract_memories_from_interaction(
    llm: LLMProvider,
    user_inputs: list[str],
    interaction_summary: str = "",
    existing_memories: Sequence[str | UserMemory] | None = None,
    max_output_tokens: int = _MAX_EXTRACTION_OUTPUT_TOKENS,
) -> list[ProposedMemory]:
    """Analyze interaction and return a deduplicated, filtered list of ProposedMemory objects."""
    clean_inputs = [inp for inp in user_inputs if inp and not is_transient_or_noise(inp)]
    if not clean_inputs:
        return []

    system_prompt = build_memory_extraction_system_prompt()
    existing_strs = [m.content if isinstance(m, UserMemory) else str(m) for m in (existing_memories or [])]
    user_prompt = build_memory_extraction_user_prompt(
        interaction_summary=interaction_summary,
        user_inputs=clean_inputs,
        existing_memories=existing_strs,
    )

    try:
        response = await llm.generate(
            [Message(role="user", content=user_prompt)],
            system=system_prompt,
            max_tokens=max_output_tokens,
            temperature=0.1,
        )
        raw_text = response.text or ""
        payload = extract_json_object(raw_text)
    except (JSONExtractionError, Exception) as exc:
        logger.debug("Memory extraction LLM generation or JSON extraction failed: %s", exc)
        return []

    raw_memories = payload.get("memories", [])
    candidates: list[ProposedMemory] = []
    for m in raw_memories:
        try:
            candidates.append(ProposedMemory.model_validate(m))
        except Exception as parse_err:
            logger.debug("Skipping invalid memory proposal candidate %s: %s", m, parse_err)

    return deduplicate_and_filter_proposals(
        candidates,
        existing_memories=existing_strs,
        min_importance=0.50,
    )


async def extract_and_propose_memories_async(
    *,
    llm: LLMProvider,
    memory_client: MemoryClient,
    user_id: UUID | str | None,
    map_id: UUID | str | None,
    user_inputs: list[str],
    interaction_summary: str = "",
    existing_memories: Sequence[str | UserMemory] | None = None,
) -> int:
    """Asynchronous background coroutine to extract and propose memories.

    Runs fully non-blocking and safe: catches all exceptions and logs warnings.
    Returns the count of successfully stored proposals.
    """
    if not user_id:
        logger.debug("Skipping memory extraction: no user_id available.")
        return 0

    try:
        proposals = await extract_memories_from_interaction(
            llm=llm,
            user_inputs=user_inputs,
            interaction_summary=interaction_summary,
            existing_memories=existing_memories,
        )
        if not proposals:
            return 0

        logger.info(
            "Extracted %d memory proposals for user %s (map %s)",
            len(proposals),
            user_id,
            map_id,
        )

        stored_count = 0
        for p in proposals:
            res = await memory_client.propose_memory(
                user_id=user_id,
                content=p.content,
                memory_type=p.memory_type,
                map_id=map_id,
            )
            if res.stored:
                stored_count += 1
                logger.info("Durable memory stored for user %s: '%s'", user_id, p.content[:60])

        return stored_count
    except Exception as exc:
        logger.warning("Background memory extraction and proposal failed: %s", exc)
        return 0
    finally:
        await memory_client.aclose()


def schedule_memory_extraction(
    *,
    llm: LLMProvider,
    base_url: str,
    token: str,
    user_id: UUID | str | None,
    map_id: UUID | str | None,
    user_inputs: list[str],
    interaction_summary: str = "",
    existing_memories: Sequence[str | UserMemory] | None = None,
) -> asyncio.Task | None:
    """Convenience helper to schedule memory extraction as a fire-and-forget background task."""
    if not user_id or not base_url:
        return None

    client = MemoryClient(base_url=base_url, token=token)
    task = asyncio.create_task(
        extract_and_propose_memories_async(
            llm=llm,
            memory_client=client,
            user_id=user_id,
            map_id=map_id,
            user_inputs=user_inputs,
            interaction_summary=interaction_summary,
            existing_memories=existing_memories,
        )
    )
    return task
