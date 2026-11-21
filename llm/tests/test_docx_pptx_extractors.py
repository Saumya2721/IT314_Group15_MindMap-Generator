"""Unit tests for DOCX and PPTX document extractors and security guards."""

from __future__ import annotations

import io
import zipfile

import docx
import pptx
import pytest
from ingestion.extractors import (
    FileSizeLimitExceededError,
    SeedExtractionError,
    extract_text_from_docx,
    extract_text_from_pptx,
    get_default_registry,
    reset_default_registry,
    validate_office_archive,
)
from rag.chunking import StructureAwareChunker


@pytest.fixture(autouse=True)
def clean_registry():
    reset_default_registry()
    yield
    reset_default_registry()


def _create_sample_docx(
    title: str = "Document Title",
    h1: str = "Section 1 Overview",
    h2: str = "Component Specifications",
    paragraph: str = "This is a detailed paragraph explaining system specs.",
    bullet: str = "Feature A",
) -> bytes:
    """Create in-memory .docx bytes with headings, paragraphs, and tables."""
    doc = docx.Document()
    doc.add_heading(title, level=0)  # Title
    doc.add_heading(h1, level=1)  # Heading 1
    doc.add_paragraph(paragraph)
    doc.add_heading(h2, level=2)  # Heading 2
    doc.add_paragraph(bullet, style="List Bullet")

    # Add a table
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Param"
    table.cell(0, 1).text = "Value"
    table.cell(1, 0).text = "Timeout"
    table.cell(1, 1).text = "30s"

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _create_sample_pptx(
    title1: str = "Architecture Deck",
    bullet1: str = "First key point",
    bullet2: str = "Second key point",
    title2: str = "Performance Metrics",
    bullet3: str = "Sub-second response time",
) -> bytes:
    """Create in-memory .pptx bytes with multiple slides and bullet items."""
    prs = pptx.Presentation()

    # Slide 1: Title slide
    title_layout = prs.slide_layouts[0]
    slide1 = prs.slides.add_slide(title_layout)
    slide1.shapes.title.text = title1
    slide1.placeholders[1].text = f"{bullet1}\n{bullet2}"

    # Slide 2: Content slide
    content_layout = prs.slide_layouts[1]
    slide2 = prs.slides.add_slide(content_layout)
    slide2.shapes.title.text = title2
    slide2.placeholders[1].text = bullet3

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def test_docx_extractor_converts_heading_styles_and_paragraphs():
    docx_bytes = _create_sample_docx()
    extracted = extract_text_from_docx(docx_bytes)

    assert "# Document Title" in extracted
    assert "# Section 1 Overview" in extracted
    assert "## Component Specifications" in extracted
    assert "This is a detailed paragraph explaining system specs." in extracted
    assert "- Feature A" in extracted
    assert "| Param | Value |" in extracted
    assert "| Timeout | 30s |" in extracted


def test_docx_empty_document_raises():
    doc = docx.Document()
    buf = io.BytesIO()
    doc.save(buf)

    with pytest.raises(SeedExtractionError, match="No readable text found in Word document"):
        extract_text_from_docx(buf.getvalue())


def test_pptx_extractor_converts_slide_titles_and_bullets():
    pptx_bytes = _create_sample_pptx()
    extracted = extract_text_from_pptx(pptx_bytes)

    assert "--- Slide 1 ---" in extracted
    assert "## Architecture Deck" in extracted
    assert "- First key point" in extracted
    assert "- Second key point" in extracted

    assert "--- Slide 2 ---" in extracted
    assert "## Performance Metrics" in extracted
    assert "- Sub-second response time" in extracted


def test_pptx_empty_presentation_raises():
    prs = pptx.Presentation()
    buf = io.BytesIO()
    prs.save(buf)

    with pytest.raises(SeedExtractionError, match="No readable text found in PowerPoint"):
        extract_text_from_pptx(buf.getvalue())


def test_security_rejects_macro_vbaproject_bin():
    # Build a zip archive containing vbaProject.bin
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types></Types>")
        zf.writestr("word/vbaProject.bin", b"\x00" * 100)

    macro_data = buf.getvalue()
    with pytest.raises(SeedExtractionError, match="macros are not permitted"):
        validate_office_archive(macro_data)

    with pytest.raises(SeedExtractionError, match="macros are not permitted"):
        extract_text_from_docx(macro_data)


def test_security_rejects_macro_content_types():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<Types><Override PartName="/word/document.xml" '
            'ContentType="application/vnd.ms-word.document.macroEnabled.main+xml"/></Types>',
        )
        zf.writestr("word/document.xml", "<document></document>")

    macro_data = buf.getvalue()
    with pytest.raises(SeedExtractionError, match="macros are not permitted"):
        validate_office_archive(macro_data)


def test_security_enforces_zip_bomb_uncompressed_limit():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types></Types>")
        zf.writestr("huge.txt", b"x" * 1000)

    # Validate with low threshold: 500 bytes
    with pytest.raises(SeedExtractionError, match="exceeds safety limit"):
        validate_office_archive(buf.getvalue(), max_uncompressed_bytes=500)


def test_security_enforces_corrupt_archive_rejection():
    with pytest.raises(SeedExtractionError, match="Corrupt or invalid document archive"):
        validate_office_archive(b"PK\x03\x04not-a-valid-zip-content")


def test_registry_integration_docx_and_pptx():
    reg = get_default_registry()

    # Verify both extensions and MIME types are registered
    assert ".docx" in reg.get_supported_extensions()
    assert ".pptx" in reg.get_supported_extensions()
    assert "application/vnd.openxmlformats-officedocument.wordprocessingml.document" in reg.get_supported_mime_types()
    assert "application/vnd.openxmlformats-officedocument.presentationml.presentation" in reg.get_supported_mime_types()

    docx_data = _create_sample_docx(title="Registry DOCX Test", h1="Registry Heading")
    pptx_data = _create_sample_pptx(title1="Registry PPTX Test", bullet1="Bullet via registry")

    # Extract through registry
    docx_res = reg.extract(docx_data, filename="sample.docx")
    assert "# Registry DOCX Test" in docx_res
    assert "# Registry Heading" in docx_res

    pptx_res = reg.extract(pptx_data, filename="sample.pptx")
    assert "## Registry PPTX Test" in pptx_res
    assert "- Bullet via registry" in pptx_res


def test_structure_aware_chunker_parses_docx_and_pptx_output():
    docx_data = _create_sample_docx(title="System Guide", h1="1. Introduction", h2="1.1 Overview")
    pptx_data = _create_sample_pptx(title1="Project Launch", bullet1="Scope overview")

    reg = get_default_registry()
    docx_text = reg.extract(docx_data, filename="guide.docx")
    pptx_text = reg.extract(pptx_data, filename="launch.pptx")

    chunker = StructureAwareChunker(chunk_size=300)

    docx_chunks = chunker.chunk(docx_text)
    assert len(docx_chunks) > 0
    assert any("Introduction" in (c.section_path or "") or "Overview" in (c.section_path or "") for c in docx_chunks)

    pptx_chunks = chunker.chunk(pptx_text)
    assert len(pptx_chunks) > 0
    assert any("Project Launch" in (c.section_path or "") for c in pptx_chunks)


def test_oversized_docx_rejection_through_registry():
    reg = get_default_registry()
    spec = reg.get(filename="test.docx")
    assert spec is not None

    # Oversized document rejection
    fake_data = b"PK\x03\x04" + b"0" * (spec.max_size_bytes + 10)
    with pytest.raises(FileSizeLimitExceededError, match="File exceeds maximum allowed size"):
        reg.extract(fake_data, filename="oversized.docx")
