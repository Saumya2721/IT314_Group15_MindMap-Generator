"""
Per-type content extraction. Kept as separate functions per DR/US-02
technical note: "Parsing should remain distinct for each input type."
Each extractor returns plain text (or raises SeedExtractionError) —
callers never need to know whether a PDF was OCR'd or text-parsed.
"""

from __future__ import annotations

import hashlib
import io
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pypdf import PdfReader


class SeedExtractionError(Exception):
    """Raised for a malformed, corrupt, or unreadable upload. Caught by
    seed/normalizer.py and turned into a Scenario-4-style clear message —
    never propagates as a raw parser traceback to the user."""


def checksum_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def detect_structural_cues(
    text: str,
    running_headers: set[str] | None = None,
    running_footers: set[str] | None = None,
) -> str:
    """Detect structural cues in plain text or PDF page content.

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


def extract_pdf_outline(reader: PdfReader) -> str:
    """Extract PDF outline / bookmarks hierarchy formatted as a markdown Table of Contents."""
    try:
        outline = getattr(reader, "outline", None)
        if not outline:
            return ""

        toc_lines: list[str] = ["## Table of Contents"]

        def _traverse(items: list[Any], depth: int = 0) -> None:
            for item in items:
                if isinstance(item, list):
                    _traverse(item, depth + 1)
                else:
                    title = getattr(item, "title", None)
                    if not title and isinstance(item, dict):
                        title = item.get("/Title")
                    if not title:
                        title = str(item)
                    title = title.strip()
                    if not title:
                        continue

                    page_str = ""
                    try:
                        page_num = reader.get_destination_page_number(item)
                        if page_num is not None:
                            page_str = f" (Page {page_num + 1})"
                    except Exception:
                        pass

                    indent = "  " * depth
                    toc_lines.append(f"{indent}- {title}{page_str}")

        if isinstance(outline, list):
            _traverse(outline, 0)

        if len(toc_lines) > 1:
            return "\n".join(toc_lines)
    except Exception:
        pass
    return ""


def extract_text_from_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pypdf not installed. `pip install pypdf`.") from exc

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise SeedExtractionError("This PDF is password-protected and can't be read.")

        toc = extract_pdf_outline(reader)
        pages_raw: list[tuple[int, str]] = []
        top_candidates: list[str] = []
        bottom_candidates: list[str] = []
        for idx, page in enumerate(reader.pages, start=1):
            raw = page.extract_text() or ""
            if raw.strip():
                pages_raw.append((idx, raw.strip()))
                non_empty = [ln.strip() for ln in raw.strip().split("\n") if ln.strip()]
                if non_empty:
                    # Collect up to 2 candidates from top of each page
                    for top_ln in non_empty[:2]:
                        top_candidates.append(top_ln)
                    # Collect up to 2 candidates from bottom of each page
                    for btm_ln in non_empty[-2:]:
                        bottom_candidates.append(btm_ln)

        # Detect running headers and recurring footers (lines appearing across 2+ pages)
        running_headers: set[str] = set()
        running_footers: set[str] = set()
        if len(pages_raw) >= 2:
            from collections import Counter

            top_counts = Counter(top_candidates)
            running_headers = {line for line, cnt in top_counts.items() if cnt >= 2}

            bottom_counts = Counter(bottom_candidates)
            running_footers = {line for line, cnt in bottom_counts.items() if cnt >= 2}

        # Common page numbering regex (e.g. "Page 1 of 5", "1 / 10", standalone digits)
        page_num_regex = re.compile(
            r"^(?:page\s+\d+(?:\s*(?:of|/)\s*\d+)?|\d+\s*(?:of|/)\s*\d+|\d+)$",
            re.IGNORECASE,
        )

        pages_text: list[str] = []
        for idx, raw_content in pages_raw:
            raw_lines = raw_content.split("\n")
            filtered_raw_lines: list[str] = []
            for ln_idx, ln in enumerate(raw_lines):
                st = ln.strip()
                # If in the bottom lines and matches page number pattern, skip
                if ln_idx >= len(raw_lines) - 2 and page_num_regex.match(st):
                    continue
                filtered_raw_lines.append(ln)
            clean_raw = "\n".join(filtered_raw_lines)

            structured = detect_structural_cues(
                clean_raw,
                running_headers=running_headers,
                running_footers=running_footers,
            )
            pages_text.append(f"--- Page {idx} ---\n{structured}")

    except SeedExtractionError:
        raise
    except Exception as exc:
        raise SeedExtractionError(f"Couldn't parse this PDF: {exc}") from exc

    if not pages_text:
        # No extractable text layer — a scanned PDF. Sprint 1 does not include
        # OCR; flag it clearly rather than silently returning an empty seed.
        raise SeedExtractionError(
            "This PDF has no extractable text (it may be a scanned image). "
            "OCR support is not yet enabled — try uploading it as an image instead, "
            "or a text-based PDF."
        )

    parts: list[str] = []
    if toc:
        parts.append(toc)
    parts.extend(pages_text)
    return "\n\n".join(parts)


def extract_text_from_image(data: bytes) -> str:
    try:
        import pytesseract
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:  # pragma: no cover
        raise SeedExtractionError("pytesseract/Pillow not installed. OCR support unavailable.") from exc

    try:
        image = Image.open(io.BytesIO(data))
        image.verify()
        image = Image.open(io.BytesIO(data))  # re-open: verify() consumes the file pointer
    except UnidentifiedImageError as exc:
        raise SeedExtractionError("This file isn't a readable image.") from exc
    except Exception as exc:
        raise SeedExtractionError(f"Couldn't open this image: {exc}") from exc

    try:
        text = pytesseract.image_to_string(image)
    except Exception as exc:
        raise SeedExtractionError(f"OCR failed on this image: {exc}") from exc

    if not text.strip():
        raise SeedExtractionError(
            "No readable text was found in this image. If it contains a diagram or "
            "photo rather than text, describe it in the free-text field instead."
        )
    return text.strip()


def normalize_free_text(topic: str | None, ideas: str | None) -> str:
    parts = [p.strip() for p in (topic, ideas) if p and p.strip()]
    if not parts:
        raise SeedExtractionError("No topic or free-text ideas were provided.")
    raw_text = "\n\n".join(parts)
    return detect_structural_cues(raw_text)
