"""
Deduplication and importance scoring logic for User Memories (S3-L3).

Enforces:
1. Exact duplicate detection (normalized whitespace and lowercase).
2. Near-duplicate / semantic redundancy detection via token Jaccard similarity.
3. Importance scoring evaluating durability, cross-map utility, and recurrence.
4. Filtering of temporary or low-value memories (< min_importance).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from memory.schemas import ProposedMemory, UserMemory

# Consistent with backend S3-BE4 Jaccard threshold
JACCARD_SIMILARITY_THRESHOLD = 0.80

# Keywords signalling transient/temporary requests that should NOT be durable memories
_TRANSIENT_PATTERNS = [
    r"\b(zoom\s*in|zoom\s*out|pan|scroll)\b",
    r"\b(change\s+(node\s+)?color\s+to\s+\w+)\b",
    r"\b(delete\s+(this\s+)?node\s+\d+)\b",
    r"\b(undo|redo)\b",
    r"\b(move\s+node\s+\w+\s+to\s+x=\d+)\b",
    r"\b(test(ing)?\s+123|hello|hi|hey|good\s+morning)\b",
    r"\b(please\s+regenerate\s+just\s+node\s+\d+)\b",
]

_TRANSIENT_REGEX = re.compile("|".join(_TRANSIENT_PATTERNS), re.IGNORECASE)


def normalize_memory_text(text: str) -> str:
    """Normalize text by trimming, collapsing spaces, and lowercasing."""
    return re.sub(r"\s+", " ", text.strip().lower())


def token_jaccard_similarity(text_a: str, text_b: str) -> float:
    """Calculate token-level Jaccard similarity between two text strings."""
    tokens_a = set(normalize_memory_text(text_a).split())
    tokens_b = set(normalize_memory_text(text_b).split())
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def is_exact_duplicate(candidate: str, existing: Sequence[str | UserMemory | ProposedMemory]) -> bool:
    """Check if candidate text is identical to any existing memory after normalization."""
    norm_candidate = normalize_memory_text(candidate)
    for item in existing:
        text = item.content if isinstance(item, (UserMemory, ProposedMemory)) else str(item)
        if normalize_memory_text(text) == norm_candidate:
            return True
    return False


def is_near_duplicate(
    candidate: str,
    existing: Sequence[str | UserMemory | ProposedMemory],
    threshold: float = JACCARD_SIMILARITY_THRESHOLD,
) -> bool:
    """Check if candidate text has token Jaccard similarity >= threshold with any existing memory."""
    for item in existing:
        text = item.content if isinstance(item, (UserMemory, ProposedMemory)) else str(item)
        if token_jaccard_similarity(candidate, text) >= threshold:
            return True
    return False


def is_transient_or_noise(text: str) -> bool:
    """Detect if text describes a one-off UI action or transient request."""
    if _TRANSIENT_REGEX.search(text):
        return True
    # Too short to be a meaningful durable memory
    if len(text.strip().split()) < 3 and len(text.strip()) < 12:
        return True
    return False


def calculate_importance_score(
    content: str,
    memory_type: str = "preference",
    *,
    is_durable: bool = True,
    is_recurring: bool = False,
) -> float:
    """Calculate importance score between 0.0 and 1.0.

    Evaluation factors:
    - Durability (is it durable across multiple mind maps / topics?)
    - Specificity & Length (detailed domain preferences score higher)
    - Type weight (preferences > decisions > facts > general context)
    - Recurrence boost
    """
    if not is_durable or is_transient_or_noise(content):
        return 0.2

    # Baseline weight by type
    type_weights = {
        "preference": 0.80,  # e.g. "User prefers bullet points and visual flow"
        "decision": 0.75,  # e.g. "Use formal academic terminology"
        "convention": 0.75,  # e.g. "Always include DOI in source citations"
        "fact": 0.65,  # e.g. "User is a bioinformatics researcher"
    }
    base = type_weights.get(memory_type.lower(), 0.60)

    # Specificity bonus: longer, content-rich statements convey clearer durable value
    word_count = len(content.split())
    if 5 <= word_count <= 25:
        base += 0.05
    elif word_count > 25:
        base += 0.02

    # Recurrence bonus
    if is_recurring:
        base += 0.10

    return min(1.0, max(0.0, round(base, 2)))


def deduplicate_and_filter_proposals(
    proposals: Sequence[ProposedMemory],
    existing_memories: Sequence[str | UserMemory | ProposedMemory],
    min_importance: float = 0.50,
) -> list[ProposedMemory]:
    """Filter out duplicates, near-duplicates, transient noise, and low-importance proposals.

    Also deduplicates within the candidate batch itself.
    """
    accepted: list[ProposedMemory] = []
    seen: list[str] = [
        item.content if isinstance(item, (UserMemory, ProposedMemory)) else str(item) for item in existing_memories
    ]

    for p in proposals:
        if not p.is_durable or is_transient_or_noise(p.content):
            continue

        score = p.importance_score if p.importance_score > 0 else calculate_importance_score(p.content, p.memory_type)
        if score < min_importance:
            continue

        # Check against existing + already accepted in this batch
        if is_exact_duplicate(p.content, seen) or is_near_duplicate(p.content, seen):
            continue

        p_copy = p.model_copy(update={"importance_score": score})
        accepted.append(p_copy)
        seen.append(p.content)

    return accepted
