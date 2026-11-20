"""Unit tests for the extensible ExtractorRegistry and multi-format ingestion."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from contracts.models import SeedAssetType
from ingestion.extractors.base import (
    ExtractionTimeoutError,
    ExtractorSpec,
    FileSizeLimitExceededError,
    SeedExtractionError,
    UnsupportedMediaTypeError,
)
from ingestion.extractors.registry import (
    ExtractorRegistry,
    get_default_registry,
    reset_default_registry,
)
from rag.chunking import StructureAwareChunker
from seed.normalizer import SeedSubmission, UploadedFile, normalize_seed


@pytest.fixture(autouse=True)
def clean_registry():
    reset_default_registry()
    yield
    reset_default_registry()


def test_registry_lookup_by_exact_mime():
    reg = ExtractorRegistry()
    spec = ExtractorSpec(
        name="test_doc",
        mime_types=["application/x-custom-doc"],
        extensions=[".cdoc"],
        handler=lambda data: "# Heading\nCustom text",
    )
    reg.register(spec)

    found = reg.get(mime_type="application/x-custom-doc")
    assert found is not None
    assert found.name == "test_doc"


def test_registry_lookup_by_wildcard_mime():
    reg = ExtractorRegistry()
    spec = ExtractorSpec(
        name="wildcard_image",
        mime_types=["image/*"],
        extensions=[".img"],
        handler=lambda data: "# Image\nImage text",
    )
    reg.register(spec)

    found = reg.get(mime_type="image/bmp")
    assert found is not None
    assert found.name == "wildcard_image"


def test_registry_lookup_by_extension_fallback():
    reg = ExtractorRegistry()
    spec = ExtractorSpec(
        name="custom_pdf",
        mime_types=["application/pdf"],
        extensions=[".pdf"],
        handler=lambda data: "# PDF\nPDF text",
    )
    reg.register(spec)

    # Missing MIME type -> extension lookup
    found = reg.get(mime_type=None, filename="report.pdf")
    assert found is not None
    assert found.name == "custom_pdf"

    # Generic octet-stream MIME -> extension lookup
    found_generic = reg.get(mime_type="application/octet-stream", filename="archive.PDF")
    assert found_generic is not None
    assert found_generic.name == "custom_pdf"


def test_registry_unsupported_type_raises():
    reg = ExtractorRegistry()
    assert reg.get("application/unknown", "file.xyz") is None
    assert not reg.is_supported("application/unknown", "file.xyz")

    with pytest.raises(UnsupportedMediaTypeError, match="Unsupported file type"):
        reg.extract(b"dummy bytes", mime_type="application/unknown", filename="file.xyz")


def test_registry_empty_file_rejected():
    reg = get_default_registry()
    with pytest.raises(SeedExtractionError, match="empty"):
        reg.extract(b"", mime_type="application/pdf", filename="empty.pdf")


def test_registry_size_limit_rejection():
    reg = ExtractorRegistry()
    spec = ExtractorSpec(
        name="small_doc",
        mime_types=["application/x-small"],
        extensions=[".sml"],
        handler=lambda data: "# Doc\nContent",
        max_size_bytes=100,  # 100 bytes limit
    )
    reg.register(spec)

    oversized = b"A" * 105
    with pytest.raises(FileSizeLimitExceededError, match="File exceeds maximum allowed size"):
        reg.extract(oversized, mime_type="application/x-small", filename="test.sml")


def test_registry_timeout_enforcement():
    reg = ExtractorRegistry()

    def slow_extractor(data: bytes) -> str:
        time.sleep(0.5)
        return "# Slow\nSlow text"

    spec = ExtractorSpec(
        name="slow_doc",
        mime_types=["application/x-slow"],
        extensions=[".slow"],
        handler=slow_extractor,
        timeout_seconds=0.1,  # 100ms cap
    )
    reg.register(spec)

    with pytest.raises(ExtractionTimeoutError, match="timed out"):
        reg.extract(b"data", mime_type="application/x-slow", filename="doc.slow")


def test_registry_fallback_handler_on_primary_failure():
    reg = ExtractorRegistry()

    def failing_primary(data: bytes) -> str:
        raise ValueError("Primary service unavailable")

    def working_fallback(data: bytes) -> str:
        return "## Fallback Heading\nRecovered via fallback handler"

    spec = ExtractorSpec(
        name="robust_extractor",
        mime_types=["application/x-robust"],
        extensions=[".rob"],
        handler=failing_primary,
        fallback_handler=working_fallback,
    )
    reg.register(spec)

    result = reg.extract(b"dummy bytes", mime_type="application/x-robust", filename="test.rob")
    assert "## Fallback Heading" in result
    assert "Recovered via fallback handler" in result


def test_registry_fallback_handler_on_empty_primary_result():
    reg = ExtractorRegistry()

    def empty_primary(data: bytes) -> str:
        return "   "

    def working_fallback(data: bytes) -> str:
        return "## Fallback Header\nContent recovered"

    spec = ExtractorSpec(
        name="empty_fallback",
        mime_types=["application/x-empty"],
        extensions=[".emp"],
        handler=empty_primary,
        fallback_handler=working_fallback,
    )
    reg.register(spec)

    result = reg.extract(b"dummy bytes", mime_type="application/x-empty", filename="test.emp")
    assert "## Fallback Header" in result
    assert "Content recovered" in result


def test_registry_derived_upload_allowlists():
    reg = get_default_registry()
    mime_types = reg.get_supported_mime_types()
    extensions = reg.get_supported_extensions()
    allowlist = reg.get_upload_allowlist()

    assert "application/pdf" in mime_types
    assert "image/png" in mime_types
    assert "text/plain" in mime_types

    assert ".pdf" in extensions
    assert ".png" in extensions
    assert ".txt" in extensions

    assert allowlist["max_file_size_bytes"] >= 20 * 1024 * 1024
    assert "pdf" in allowlist["per_type_limits"]
    assert "image" in allowlist["per_type_limits"]


def test_structure_aware_chunker_compatibility_with_registry():
    reg = get_default_registry()

    sample_doc = (
        "SYSTEM DESIGN SPECIFICATION\n"
        "This is an architectural overview.\n\n"
        "1. Core Infrastructure\n"
        "• Queue workers\n"
        "• State storage"
    )

    extracted = reg.extract(sample_doc.encode("utf-8"), mime_type="text/plain", filename="spec.txt")
    assert "## SYSTEM DESIGN SPECIFICATION" in extracted
    assert "## 1. Core Infrastructure" in extracted
    assert "- Queue workers" in extracted

    # Verify StructureAwareChunker parses headings into hierarchy
    chunker = StructureAwareChunker(chunk_size=500)
    chunks = chunker.chunk(extracted)
    assert len(chunks) > 0
    # Chunker captures the section headings in breadcrumbs / section_path
    assert any(
        "SYSTEM DESIGN SPECIFICATION" in (c.section_path or "") or "Core Infrastructure" in (c.section_path or "")
        for c in chunks
    )


@pytest.mark.asyncio
async def test_async_extraction_with_timeout_and_size_guards():
    reg = ExtractorRegistry()
    spec = ExtractorSpec(
        name="async_doc",
        mime_types=["application/x-async"],
        extensions=[".asy"],
        handler=lambda data: "## Async Header\nAsync text",
        timeout_seconds=2.0,
    )
    reg.register(spec)

    result = await reg.aextract(b"data", mime_type="application/x-async", filename="doc.asy")
    assert "## Async Header" in result
    assert "Async text" in result


@pytest.mark.asyncio
async def test_normalizer_integrates_seamlessly_with_registry():
    mock_pdf_page = MagicMock()
    mock_pdf_page.extract_text.return_value = "OVERVIEW\nPDF page 1 details."

    mock_reader = MagicMock()
    mock_reader.is_encrypted = False
    mock_reader.pages = [mock_pdf_page]
    mock_reader.outline = None

    with patch("pypdf.PdfReader", return_value=mock_reader):
        sub = SeedSubmission(
            topic="Project Alpha",
            ideas="Brainstorming core features",
            files=[
                UploadedFile(
                    file_name="alpha.pdf",
                    mime_type="application/pdf",
                    data=b"%PDF-1.4 mock",
                    storage_key="user/map/job/alpha.pdf",
                )
            ],
        )

        normalized = await normalize_seed(submission=sub)
        assert len(normalized.rejected_files) == 0
        assert len(normalized.seed_assets) == 2  # 1 text, 1 PDF asset
        pdf_asset = next(a for a in normalized.seed_assets if a.asset_type == SeedAssetType.PDF)
        assert pdf_asset.file_name == "alpha.pdf"
        assert "## OVERVIEW" in pdf_asset.content_text
        assert "PDF page 1 details." in pdf_asset.content_text
        assert "Project Alpha" in normalized.combined_text
