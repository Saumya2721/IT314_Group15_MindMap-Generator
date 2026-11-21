"""PowerPoint (.pptx) presentation extractor converting slide titles into headings and bullets into text."""

from __future__ import annotations

import io
from typing import Any

from ingestion.extractors.base import (
    SeedExtractionError,
    detect_structural_cues,
)
from ingestion.extractors.office_security import validate_office_archive


def extract_text_from_pptx(data: bytes, **kwargs: Any) -> str:
    """Extract text from a PowerPoint presentation (.pptx) converting slide titles into Markdown headings.

    Security:
    - Enforces zip-bomb uncompressed size limits
    - Rejects macro-enabled presentations
    """
    validate_office_archive(data)

    try:
        import pptx
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("python-pptx not installed. Run `uv add python-pptx`.") from exc

    try:
        prs = pptx.Presentation(io.BytesIO(data))
    except Exception as exc:
        raise SeedExtractionError(f"Failed to parse PowerPoint presentation: {exc}") from exc

    slides_text: list[str] = []

    for idx, slide in enumerate(prs.slides, start=1):
        slide_lines: list[str] = [f"--- Slide {idx} ---"]

        title_shape = slide.shapes.title
        if title_shape and title_shape.text.strip():
            slide_lines.append(f"## {title_shape.text.strip()}")

        for shape in slide.shapes:
            if shape == title_shape:
                continue

            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    p_text = paragraph.text.strip()
                    if not p_text:
                        continue
                    indent = "  " * paragraph.level
                    slide_lines.append(f"{indent}- {p_text}")

            elif shape.has_table:
                table_rows: list[str] = []
                for r_idx, row in enumerate(shape.table.rows):
                    cell_texts = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                    if not any(cell_texts):
                        continue
                    table_rows.append("| " + " | ".join(cell_texts) + " |")
                    if r_idx == 0:
                        table_rows.append("| " + " | ".join(["---"] * len(cell_texts)) + " |")
                if table_rows:
                    slide_lines.append("\n".join(table_rows))

        # Only append slide if it contains content beyond the slide marker
        if len(slide_lines) > 1:
            slide_content = "\n".join(slide_lines)
            slides_text.append(detect_structural_cues(slide_content))

    if not slides_text:
        raise SeedExtractionError("No readable text found in PowerPoint presentation.")

    return "\n\n".join(slides_text)
