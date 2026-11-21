"""
User Memory Extraction, Scoring, and Retrieval System (S3-L3).

Exports:
- MemoryClient: HTTP client for backend S3-BE4 memory endpoints.
- UserMemory, ProposedMemory: Typed schemas.
- inject_memories_into_system_prompt, select_top_k_relevant_memories: Prompt formatting.
- deduplicate_and_filter_proposals, calculate_importance_score: Deduplication & scoring.
- extract_memories_from_interaction, schedule_memory_extraction: Async extraction.
"""

from memory.client import MemoryClient
from memory.extraction import (
    build_memory_extraction_system_prompt,
    build_memory_extraction_user_prompt,
    extract_and_propose_memories_async,
    extract_memories_from_interaction,
    schedule_memory_extraction,
)
from memory.integration import fire_and_forget_memory_extraction, retrieve_and_inject_memories  # noqa: F401
from memory.prompt import (
    get_memory_token_ceiling,
    inject_memories_into_system_prompt,
    select_top_k_relevant_memories,
)
from memory.schemas import (
    MemoryExtractionPayload,
    MemoryProposalRequest,
    MemoryProposalResponse,
    ProposedMemory,
    UserMemory,
)
from memory.scoring import (
    calculate_importance_score,
    deduplicate_and_filter_proposals,
    is_exact_duplicate,
    is_near_duplicate,
    is_transient_or_noise,
    normalize_memory_text,
    token_jaccard_similarity,
)

__all__ = [
    "MemoryClient",
    "UserMemory",
    "ProposedMemory",
    "MemoryProposalRequest",
    "MemoryProposalResponse",
    "MemoryExtractionPayload",
    "inject_memories_into_system_prompt",
    "select_top_k_relevant_memories",
    "get_memory_token_ceiling",
    "deduplicate_and_filter_proposals",
    "calculate_importance_score",
    "is_exact_duplicate",
    "is_near_duplicate",
    "is_transient_or_noise",
    "normalize_memory_text",
    "token_jaccard_similarity",
    "extract_memories_from_interaction",
    "extract_and_propose_memories_async",
    "schedule_memory_extraction",
    "build_memory_extraction_system_prompt",
    "build_memory_extraction_user_prompt",
]
