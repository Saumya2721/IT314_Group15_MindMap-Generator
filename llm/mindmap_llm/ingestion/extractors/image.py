"""Image text extractor with OCR and cue normalization."""

from __future__ import annotations

import io
from typing import Any

from ingestion.extractors.base import (
    SeedExtractionError,
    detect_structural_cues,
)


def extract_text_from_image(data: bytes, **kwargs: Any) -> str:
    """Extract text from image bytes using OCR, normalizing cues for StructureAwareChunker."""
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

    # Convert structural cues (ALL-CAPS headings, numbered lists, bullet points)
    normalized = detect_structural_cues(text.strip())
    return normalized
