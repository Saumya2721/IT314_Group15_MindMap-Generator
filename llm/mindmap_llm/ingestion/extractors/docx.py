"""Word (.docx) document extractor converting Word heading styles into Markdown headings."""

from __future__ import annotations

import io
import re
from typing import Any

from ingestion.extractors.base import (
    SeedExtractionError,
    detect_structural_cues,
)
from ingestion.extractors.office_security import validate_office_archive


def _map_docx_heading_level(style_name: str) -> int | None:
    """Extract heading level from Word style name."""
    clean = style_name.strip().lower()
    if clean == "title":
        return 1
    if clean == "subtitle":
        return 2

    # Check for "heading 1", "heading 2", etc.
    match = re.match(r"^heading\s*(\d+)$", clean)
    if match:
        level = int(match.group(1))
        return min(max(level, 1), 6)
    return None


def extract_text_from_docx(data: bytes, **kwargs: Any) -> str:
    """Extract text from a Word document (.docx) converting styles to Markdown for StructureAwareChunker.

    Security:
    - Enforces zip-bomb uncompressed size limits
    - Rejects macro-enabled documents
    """
    validate_office_archive(data)

    try:
        import docx
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("python-docx not installed. Run `uv add python-docx`.") from exc

    try:
        doc = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise SeedExtractionError(f"Failed to parse Word document: {exc}") from exc

    parts: list[str] = []

    # Extract paragraphs and convert heading styles
    for p in doc.paragraphs:
        text = p.text.strip()
        if not text:
            continue

        style_name = p.style.name if p.style else ""
        heading_level = _map_docx_heading_level(style_name)

        if heading_level is not None:
            prefix = "#" * heading_level
            parts.append(f"{prefix} {text}")
        elif style_name.lower().startswith("list bullet"):
            parts.append(f"- {text}")
        elif style_name.lower().startswith("list number"):
            parts.append(f"1. {text}")
        else:
            parts.append(text)

    # Extract tables if present
    for table in doc.tables:
        table_rows: list[str] = []
        for r_idx, row in enumerate(table.rows):
            cell_texts = [cell.text.strip().replace("\n", " ") for cell in row.cells]
            if not any(cell_texts):
                continue
            table_rows.append("| " + " | ".join(cell_texts) + " |")
            if r_idx == 0:
                table_rows.append("| " + " | ".join(["---"] * len(cell_texts)) + " |")
        if table_rows:
            parts.append("\n".join(table_rows))

    if not parts:
        raise SeedExtractionError("No readable text found in Word document.")

    joined = "\n\n".join(parts)
    return detect_structural_cues(joined)
