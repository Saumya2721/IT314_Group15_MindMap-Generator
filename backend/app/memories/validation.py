from __future__ import annotations

import hashlib
import re
from uuid import UUID

ALLOWED_CATEGORIES: set[str] = {
    "preference",
    "fact",
    "instruction",
    "constraint",
    "general",
}

DISALLOWED_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"system\s*:\s*you\s+are", re.IGNORECASE),
]


def normalize_content(content: str) -> str:
    """Normalize text by trimming, collapsing spaces, and lowercasing for deduplication."""
    cleaned = re.sub(r"\s+", " ", content.strip())
    return cleaned.lower()


def compute_content_hash(content: str) -> str:
    """Compute deterministic SHA-256 hash of normalized content."""
    normalized = normalize_content(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def validate_memory_payload(
    content: str,
    category: str,
    scope: str,
    map_id: UUID | None,
) -> tuple[bool, str | None]:
    """Validate memory attributes, bounds, category, and scope consistency."""
    if not content or not content.strip():
        return False, "Memory content cannot be empty."

    stripped = content.strip()
    if len(stripped) < 3:
        return False, "Memory content is too short (minimum 3 characters)."
    if len(stripped) > 2000:
        return False, "Memory content exceeds maximum length of 2000 characters."

    # Check category
    normalized_cat = category.strip().lower()
    if normalized_cat not in ALLOWED_CATEGORIES:
        return (
            False,
            f"Invalid category '{category}'. Allowed: {', '.join(sorted(ALLOWED_CATEGORIES))}",
        )

    # Check scope consistency
    if scope == "global" and map_id is not None:
        return False, "Global scope cannot be associated with a map_id."
    if scope == "map" and map_id is None:
        return False, "Map scope requires a valid map_id."

    # Prompt injection check
    for pattern in DISALLOWED_PATTERNS:
        if pattern.search(stripped):
            return False, "Content contains disallowed instruction patterns."

    return True, None


def check_near_duplicate(
    new_content: str,
    existing_contents: list[str],
    similarity_threshold: float = 0.80,
) -> str | None:
    """Check if new_content is near-identical to any existing active memory using Jaccard similarity.

    Returns the matching existing content if duplicate is found, else None.
    """
    new_tokens = set(normalize_content(new_content).split())
    if not new_tokens:
        return None

    for existing in existing_contents:
        existing_tokens = set(normalize_content(existing).split())
        if not existing_tokens:
            continue
        intersection = len(new_tokens & existing_tokens)
        union = len(new_tokens | existing_tokens)
        similarity = intersection / union if union > 0 else 0.0
        if similarity >= similarity_threshold:
            return existing

    return None
