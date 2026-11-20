"""Base types, exceptions, and cue detection for extractors."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class SeedExtractionError(Exception):
    """Raised for a malformed, corrupt, or unreadable upload. Caught by
    normalizer and turned into a Scenario-4-style clear message —
    never propagates as a raw parser traceback to the user."""


class ExtractionTimeoutError(SeedExtractionError):
    """Raised when an extractor exceeds its allowed execution timeout."""


class FileSizeLimitExceededError(SeedExtractionError):
    """Raised when an upload exceeds the per-type file size limit."""


class UnsupportedMediaTypeError(SeedExtractionError):
    """Raised when an upload MIME type or extension is not registered or supported."""


@dataclass
class ExtractorSpec:
    """Specification and configuration for an extractor callable registered in the ExtractorRegistry."""

    name: str
    mime_types: list[str]
    extensions: list[str]
    handler: Callable[..., str]
    fallback_handler: Callable[..., str] | None = None
    max_size_bytes: int = 25 * 1024 * 1024  # Default 25 MB
    timeout_seconds: float = 30.0  # Default 30s execution cap
    asset_type: str = "text"
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def checksum_sha256(data: bytes) -> str:
    """Compute SHA-256 hex digest of file bytes."""
    return hashlib.sha256(data).hexdigest()


def detect_structural_cues(
    text: str,
    running_headers: set[str] | None = None,
    running_footers: set[str] | None = None,
) -> str:
    """Detect structural cues in plain text or page content.

    Converts:
    - ALL-CAPS lines into markdown headings (## HEADING), excluding recurring running headers
    - Numbered section lines (e.g. 1. Introduction, 1.1 Architecture) into markdown headings (##, ###, etc.)
    - Bullet sequence symbols (•, ●, ◦, ▪, etc.) into standard markdown list items (- Item)
    - Strips recurring footers and page numbering artifacts
    """
    if not text or not text.strip():
        return text

    lines = text.split("\n")
    processed_lines: list[str] = []

    numbered_pattern = re.compile(
        r"^(?:(?:Section|Chapter|Part)\s+)?((\d+(?:\.\d+)+)[:\.]?|(\d+)[:\.\)])\s+([A-Za-z0-9].*)$"
    )
    bullet_pattern = re.compile(r"^(\s*)[•●◦▪▫\*\+]\s+(.*)$")

    for line in lines:
        stripped = line.strip()

        # Preserve empty lines, existing markdown headings, horizontal rules, page markers
        if not stripped:
            processed_lines.append(line)
            continue
        if stripped.startswith("#") or stripped.startswith("---") or stripped.startswith("==="):
            processed_lines.append(line)
            continue

        # Skip recurring running footers
        if running_footers and stripped in running_footers:
            continue

        # Bullet sequence normalization
        bullet_match = bullet_pattern.match(line)
        if bullet_match:
            indent, content = bullet_match.groups()
            processed_lines.append(f"{indent}- {content}")
            continue

        # Numbered item heading detection (e.g. 1. Overview, 1.1 Background, 2.3.1 Architecture)
        numbered_match = numbered_pattern.match(stripped)
        if numbered_match and len(stripped) <= 120 and not stripped.endswith((".", "?", "!", ";", ",")):
            full_num = numbered_match.group(2) or numbered_match.group(3) or ""
            dot_count = full_num.rstrip(".").count(".")
            level = min(2 + dot_count, 6)
            heading_prefix = "#" * level
            processed_lines.append(f"{heading_prefix} {stripped}")
            continue

        # ALL-CAPS lines detection (skip recurring page running headers)
        alpha_chars = [c for c in stripped if c.isalpha()]
        if (
            len(stripped) <= 90
            and len(alpha_chars) >= 3
            and stripped.isupper()
            and not stripped.endswith((".", "?", "!", ";", ","))
            and not (running_headers and stripped in running_headers)
        ):
            processed_lines.append(f"## {stripped}")
            continue

        processed_lines.append(line)

    return "\n".join(processed_lines)


def normalize_free_text(topic: str | None, ideas: str | None) -> str:
    """Normalize topic and free-form notes into structured Markdown headings and bullet points."""
    parts = [p.strip() for p in (topic, ideas) if p and p.strip()]
    if not parts:
        raise SeedExtractionError("No topic or free-text ideas were provided.")
    raw_text = "\n\n".join(parts)
    return detect_structural_cues(raw_text)
