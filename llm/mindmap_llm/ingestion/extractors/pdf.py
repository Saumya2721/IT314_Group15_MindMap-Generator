"""PDF document extractor with TOC hierarchy extraction and cue normalization."""

from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING, Any

from ingestion.extractors.base import (
    SeedExtractionError,
    detect_structural_cues,
)
from tracing import trace_span

if TYPE_CHECKING:
    from pypdf import PdfReader


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


@trace_span("extract_text_from_pdf", run_type="parser")
def extract_text_from_pdf(data: bytes, **kwargs: Any) -> str:
    """Extract text from PDF bytes with outline and page-marker headings for StructureAwareChunker."""
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
