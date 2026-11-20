"""Extractor registry and document/image parser callables."""

from ingestion.extractors.base import (
    ExtractionTimeoutError,
    ExtractorSpec,
    FileSizeLimitExceededError,
    SeedExtractionError,
    UnsupportedMediaTypeError,
    checksum_sha256,
    detect_structural_cues,
    normalize_free_text,
)
from ingestion.extractors.image import extract_text_from_image
from ingestion.extractors.pdf import extract_pdf_outline, extract_text_from_pdf
from ingestion.extractors.registry import (
    ExtractorRegistry,
    extract_plain_text,
    get_default_registry,
    reset_default_registry,
)

__all__ = [
    "ExtractorRegistry",
    "ExtractorSpec",
    "ExtractionTimeoutError",
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
