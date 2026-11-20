"""Backward-compatible seed extractors interface.

Migrated to centralized ExtractorRegistry in `ingestion.extractors`.
Re-exports all extraction functions and errors without behavioral regressions.
"""

from __future__ import annotations

from ingestion.extractors import (
    ExtractionTimeoutError,
    ExtractorRegistry,
    ExtractorSpec,
    FileSizeLimitExceededError,
    SeedExtractionError,
    UnsupportedMediaTypeError,
    checksum_sha256,
    detect_structural_cues,
    extract_pdf_outline,
    extract_plain_text,
    extract_text_from_image,
    extract_text_from_pdf,
    get_default_registry,
    normalize_free_text,
    reset_default_registry,
)

__all__ = [
    "ExtractionTimeoutError",
    "ExtractorRegistry",
    "ExtractorSpec",
    "FileSizeLimitExceededError",
    "SeedExtractionError",
    "UnsupportedMediaTypeError",
    "checksum_sha256",
    "detect_structural_cues",
    "extract_pdf_outline",
    "extract_plain_text",
    "extract_text_from_image",
    "extract_text_from_pdf",
    "get_default_registry",
    "normalize_free_text",
    "reset_default_registry",
]
