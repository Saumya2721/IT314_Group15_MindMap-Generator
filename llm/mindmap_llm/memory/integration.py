"""
Memory integration helpers for ARQ worker tasks (S3-L3).

Provides two reusable async functions:
  - retrieve_and_inject_memories: fetches relevant user memories from the backend,
    selects top-k by keyword relevance, and returns a memory-enhanced system prompt.
  - fire_and_forget_memory_extraction: schedules background memory extraction
    after a completed workflow without blocking the calling coroutine.

Both functions degrade gracefully (log warning, return original prompt / None)
when the backend is unreachable, the user_id is absent, or memory features
are not configured.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from uuid import UUID

from memory.client import MemoryClient
from memory.extraction import extract_and_propose_memories_async
from memory.prompt import inject_memories_into_system_prompt, select_top_k_relevant_memories
from memory.schemas import UserMemory
from providers.base import LLMProvider

logger = logging.getLogger(__name__)

# Max memories to inject per prompt; top-k by relevance score
_DEFAULT_TOP_K = 5


async def retrieve_and_inject_memories(
    *,
    system_prompt: str,
    query: str,
    user_id: UUID | str | None,
    map_id: UUID | str | None,
    callback_base_url: str,
    callback_token: str,
    llm_provider: str,
    llm_model: str,
    top_k: int = _DEFAULT_TOP_K,
) -> tuple[str, list[UserMemory]]:
    """Retrieve user memories and inject relevant ones into the system prompt.

    Returns:
        (enhanced_system_prompt, retrieved_memories)
        - enhanced_system_prompt: original prompt if retrieval fails or no memories.
        - retrieved_memories: list of UserMemory objects retrieved (for extraction dedup).

    Never raises; on any failure returns the original system_prompt and [].
    """
    if not user_id or not callback_base_url:
        return system_prompt, []

    client = MemoryClient(base_url=callback_base_url, token=callback_token)
    try:
        all_memories = await client.retrieve_user_memories(
            user_id=user_id,
            map_id=map_id,
            limit=50,
        )
    except Exception as exc:
        logger.warning("Memory retrieval failed for user %s: %s", user_id, exc)
        return system_prompt, []
    finally:
        await client.aclose()

    if not all_memories:
        return system_prompt, []

    top_memories = select_top_k_relevant_memories(all_memories, query=query, top_k=top_k)
    if not top_memories:
        return system_prompt, all_memories

    enhanced = inject_memories_into_system_prompt(
        system_prompt,
        top_memories,
        provider=llm_provider,
        model=llm_model,
    )
    logger.info(
        "Injected %d/%d memories into system prompt for user %s",
        len(top_memories),
        len(all_memories),
        user_id,
    )
    return enhanced, all_memories


def fire_and_forget_memory_extraction(
    *,
    llm: LLMProvider,
    callback_base_url: str,
    callback_token: str,
    user_id: UUID | str | None,
    map_id: UUID | str | None,
    user_inputs: list[str],
    interaction_summary: str = "",
    existing_memories: Sequence[str | UserMemory] | None = None,
) -> asyncio.Task | None:
    """Schedule async memory extraction as a background fire-and-forget task.

    Returns the asyncio.Task or None if skipped (no user_id or base_url).
    The task will not propagate exceptions to the caller.
    """
    if not user_id or not callback_base_url:
        return None

    client = MemoryClient(base_url=callback_base_url, token=callback_token)

    async def _run() -> None:
        try:
            count = await extract_and_propose_memories_async(
                llm=llm,
                memory_client=client,
                user_id=user_id,
                map_id=map_id,
                user_inputs=user_inputs,
                interaction_summary=interaction_summary,
                existing_memories=existing_memories,
            )
            logger.info("Memory extraction complete: %d stored for user %s", count, user_id)
        except Exception as exc:
            logger.warning("Background memory extraction task raised: %s", exc)
        finally:
            await client.aclose()

    return asyncio.create_task(_run())
