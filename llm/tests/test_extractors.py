from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from seed.extractors import (
    SeedExtractionError,
    detect_structural_cues,
    extract_pdf_outline,
    extract_text_from_pdf,
    normalize_free_text,
)


def test_detect_structural_cues_all_caps_headings():
    raw_text = (
        "SYSTEM ARCHITECTURE\n"
        "This section details the primary system architecture.\n\n"
        "DATABASE SCHEMA\n"
        "Tables and relation schemas."
    )
    result = detect_structural_cues(raw_text)
    assert "## SYSTEM ARCHITECTURE" in result
    assert "## DATABASE SCHEMA" in result
    assert "This section details the primary system architecture." in result


def test_detect_structural_cues_ignores_all_caps_with_terminal_punctuation_or_too_short():
    raw_text = "WAIT FOR IT...\nYES!\nAB\nNormal sentence content."
    result = detect_structural_cues(raw_text)
    # Shouldn't convert lines ending with punctuation or having < 3 alpha chars
    assert "## WAIT FOR IT..." not in result
    assert "## YES!" not in result
    assert "## AB" not in result


def test_detect_structural_cues_numbered_headings():
    raw_text = (
        "1. Introduction\n"
        "Introduction content.\n\n"
        "1.1 System Architecture\n"
        "Architecture overview.\n\n"
        "2.3.1 Component Layer\n"
        "Detailed layer specs.\n\n"
        "Section 4.1 Security\n"
        "Security requirements."
    )
    result = detect_structural_cues(raw_text)
    assert "## 1. Introduction" in result
    assert "### 1.1 System Architecture" in result
    assert "#### 2.3.1 Component Layer" in result
    assert "### Section 4.1 Security" in result


def test_detect_structural_cues_numbered_non_headings_preserved():
    # Long sentences starting with numbers that end in periods should NOT become headings
    raw_text = "1. This is a descriptive sentence that explains a point in the text."
    result = detect_structural_cues(raw_text)
    assert not result.startswith("#")


def test_detect_structural_cues_bullet_normalization():
    raw_text = (
        "• Bullet dot item\n"
        "● Solid circle item\n"
        "◦ White circle item\n"
        "▪ Small square item\n"
        "  * Asterisk nested item\n"
        "  + Plus nested item"
    )
    result = detect_structural_cues(raw_text)
    assert "- Bullet dot item" in result
    assert "- Solid circle item" in result
    assert "- White circle item" in result
    assert "- Small square item" in result
    assert "  - Asterisk nested item" in result
    assert "  - Plus nested item" in result


def test_extract_pdf_outline_nested_hierarchy():
    mock_item1 = MagicMock()
    mock_item1.title = "Overview"

    mock_child = MagicMock()
    mock_child.title = "Deep Dive"

    mock_item2 = MagicMock()
    mock_item2.title = "Details"

    mock_reader = MagicMock()
    mock_reader.outline = [mock_item1, [mock_child], mock_item2]
    mock_reader.get_destination_page_number.side_effect = lambda item: (
        0 if item == mock_item1 else (1 if item == mock_child else 2)
    )

    toc = extract_pdf_outline(mock_reader)
    assert "## Table of Contents" in toc
    assert "- Overview (Page 1)" in toc
    assert "  - Deep Dive (Page 2)" in toc
    assert "- Details (Page 3)" in toc


def test_extract_pdf_outline_empty_or_missing():
    mock_reader = MagicMock()
    mock_reader.outline = None
    assert extract_pdf_outline(mock_reader) == ""

    mock_reader.outline = []
    assert extract_pdf_outline(mock_reader) == ""


def test_extract_text_from_pdf_with_page_markers_and_outline():
    mock_page1 = MagicMock()
    mock_page1.extract_text.return_value = "SYSTEM OVERVIEW\nPage 1 body."

    mock_page2 = MagicMock()
    mock_page2.extract_text.return_value = "1.1 Components\n• Service A"

    mock_reader = MagicMock()
    mock_reader.is_encrypted = False
    mock_reader.pages = [mock_page1, mock_page2]
    mock_reader.outline = None

    with patch("pypdf.PdfReader", return_value=mock_reader):
        text = extract_text_from_pdf(b"%PDF-mock")

    assert "--- Page 1 ---" in text
    assert "## SYSTEM OVERVIEW" in text
    assert "Page 1 body." in text

    assert "--- Page 2 ---" in text
    assert "### 1.1 Components" in text
    assert "- Service A" in text


def test_extract_text_from_pdf_encrypted_raises_error():
    mock_reader = MagicMock()
    mock_reader.is_encrypted = True

    with patch("pypdf.PdfReader", return_value=mock_reader):
        with pytest.raises(SeedExtractionError, match="password-protected"):
            extract_text_from_pdf(b"%PDF-mock")


def test_extract_text_from_pdf_scanned_empty_raises_error():
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "   "

    mock_reader = MagicMock()
    mock_reader.is_encrypted = False
    mock_reader.pages = [mock_page]
    mock_reader.outline = None

    with patch("pypdf.PdfReader", return_value=mock_reader):
        with pytest.raises(SeedExtractionError, match="no extractable text"):
            extract_text_from_pdf(b"%PDF-mock")


def test_normalize_free_text():
    res = normalize_free_text("SYSTEM DESIGN", "1.1 Architecture\n• Microservices")
    assert "## SYSTEM DESIGN" in res
    assert "### 1.1 Architecture" in res
    assert "- Microservices" in res


def test_normalize_free_text_empty_raises():
    with pytest.raises(SeedExtractionError, match="No topic or free-text ideas"):
        normalize_free_text("", "   ")
