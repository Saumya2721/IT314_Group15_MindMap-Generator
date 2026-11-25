"""Unit tests for multimodal vision model integration and Tesseract OCR fallback."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest
from config import Settings, VisionConfig
from ingestion.extractors import (
    SeedExtractionError,
    extract_text_from_image,
    extract_text_with_gemini,
    extract_text_with_openrouter,
    get_default_registry,
    reset_default_registry,
)
from PIL import Image
from rag.chunking import StructureAwareChunker


@pytest.fixture(autouse=True)
def clean_registry():
    reset_default_registry()
    yield
    reset_default_registry()


def _create_sample_image(format_name: str = "PNG") -> bytes:
    """Create a minimal 100x100 RGB image in memory."""
    img = Image.new("RGB", (100, 100), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format=format_name)
    return buf.getvalue()


def test_gemini_vision_payload_handling():
    img_bytes = _create_sample_image("PNG")

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = (
        "## Whiteboard Architecture\n- Frontend UI -> Backend API\n- Backend API -> Database\n1.1 Ingestion Engine"
    )
    mock_client.models.generate_content.return_value = mock_response

    mock_genai = MagicMock()
    mock_types = MagicMock()
    mock_types.Part.from_bytes.return_value = MagicMock()
    mock_genai.Client.return_value = mock_client
    mock_genai.types = mock_types

    with patch.dict("sys.modules", {"google.genai": mock_genai, "google.genai.types": mock_types}):
        result = extract_text_with_gemini(
            data=img_bytes,
            mime_type="image/png",
            model="gemini-2.0-flash",
            api_key="mock-gemini-key",
        )

    mock_genai.Client.assert_called_once_with(api_key="mock-gemini-key")
    call_args = mock_client.models.generate_content.call_args
    assert call_args.kwargs["model"] == "gemini-2.0-flash"
    assert len(call_args.kwargs["contents"]) == 2  # Part + prompt

    assert "## Whiteboard Architecture" in result
    assert "- Frontend UI -> Backend API" in result


def test_gemini_vision_missing_api_key_raises():
    img_bytes = _create_sample_image("PNG")
    with pytest.raises(ValueError, match="GEMINI_API_KEY is not configured"):
        extract_text_with_gemini(
            data=img_bytes,
            mime_type="image/png",
            model="gemini-2.0-flash",
            api_key="",
        )


def test_openrouter_vision_payload_handling():
    img_bytes = _create_sample_image("JPEG")

    mock_http_response = MagicMock()
    mock_http_response.json.return_value = {
        "choices": [{"message": {"content": "## System Diagram\n- Microservice A -> Queue -> Worker"}}]
    }
    mock_http_response.raise_for_status = MagicMock()

    with patch("httpx.Client.post", return_value=mock_http_response) as mock_post:
        result = extract_text_with_openrouter(
            data=img_bytes,
            mime_type="image/jpeg",
            model="google/gemini-2.0-flash-001",
            api_key="mock-openrouter-key",
            api_base="https://openrouter.ai/api/v1",
        )

    assert mock_post.called
    call_args = mock_post.call_args
    assert "https://openrouter.ai/api/v1/chat/completions" in call_args.args[0]
    payload = call_args.kwargs["json"]
    assert payload["model"] == "google/gemini-2.0-flash-001"
    assert "data:image/jpeg;base64," in payload["messages"][0]["content"][1]["image_url"]["url"]

    assert "## System Diagram" in result


def test_image_extractor_routing_gemini_primary():
    img_bytes = _create_sample_image("PNG")

    custom_settings = Settings(
        vision=VisionConfig(
            extractor="gemini",
            gemini_api_key="valid-key",
            gemini_model="gemini-2.0-flash",
        )
    )

    with patch("ingestion.extractors.image.get_settings", return_value=custom_settings):
        with patch(
            "ingestion.extractors.image.extract_text_with_gemini",
            return_value="## Whiteboard Notes\n- Point 1\n- Point 2",
        ) as mock_gemini:
            with patch("ingestion.extractors.image.extract_text_with_tesseract") as mock_tess:
                res = extract_text_from_image(img_bytes, mime_type="image/png")

    mock_gemini.assert_called_once()
    mock_tess.assert_not_called()
    assert "## Whiteboard Notes" in res


def test_image_extractor_routing_tesseract_explicit():
    img_bytes = _create_sample_image("PNG")

    custom_settings = Settings(
        vision=VisionConfig(
            extractor="tesseract",
        )
    )

    with patch("ingestion.extractors.image.get_settings", return_value=custom_settings):
        with patch("ingestion.extractors.image.extract_text_with_gemini") as mock_gemini:
            with patch(
                "ingestion.extractors.image.extract_text_with_tesseract",
                return_value="OCR SCANNED TEXT\nLine 2 details.",
            ) as mock_tess:
                res = extract_text_from_image(img_bytes, mime_type="image/png")

    mock_gemini.assert_not_called()
    mock_tess.assert_called_once()
    assert "## OCR SCANNED TEXT" in res


def test_fallback_to_tesseract_when_gemini_fails():
    img_bytes = _create_sample_image("PNG")

    custom_settings = Settings(
        vision=VisionConfig(
            extractor="gemini",
            gemini_api_key="valid-key",
        )
    )

    with patch("ingestion.extractors.image.get_settings", return_value=custom_settings):
        with patch(
            "ingestion.extractors.image.extract_text_with_gemini",
            side_effect=RuntimeError("Google API quota exhausted / unreachable"),
        ) as mock_gemini:
            with patch(
                "ingestion.extractors.image.extract_text_with_tesseract",
                return_value="Fallback Tesseract OCR Text",
            ) as mock_tess:
                res = extract_text_from_image(img_bytes, mime_type="image/png")

    mock_gemini.assert_called_once()
    mock_tess.assert_called_once()
    assert "Fallback Tesseract OCR Text" in res


def test_fallback_to_tesseract_when_gemini_returns_empty():
    img_bytes = _create_sample_image("PNG")

    custom_settings = Settings(
        vision=VisionConfig(
            extractor="gemini",
            gemini_api_key="valid-key",
        )
    )

    with patch("ingestion.extractors.image.get_settings", return_value=custom_settings):
        with patch(
            "ingestion.extractors.image.extract_text_with_gemini",
            return_value="   ",
        ) as mock_gemini:
            with patch(
                "ingestion.extractors.image.extract_text_with_tesseract",
                return_value="Recovered via Tesseract",
            ) as mock_tess:
                res = extract_text_from_image(img_bytes, mime_type="image/png")

    mock_gemini.assert_called_once()
    mock_tess.assert_called_once()
    assert "Recovered via Tesseract" in res


def test_fallback_to_tesseract_when_openrouter_fails():
    img_bytes = _create_sample_image("PNG")

    custom_settings = Settings(
        vision=VisionConfig(
            extractor="openrouter",
            openrouter_api_key="valid-key",
        )
    )

    with patch("ingestion.extractors.image.get_settings", return_value=custom_settings):
        with patch(
            "ingestion.extractors.image.extract_text_with_openrouter",
            side_effect=Exception("OpenRouter timeout"),
        ) as mock_or:
            with patch(
                "ingestion.extractors.image.extract_text_with_tesseract",
                return_value="Recovered via Tesseract from OpenRouter failure",
            ) as mock_tess:
                res = extract_text_from_image(img_bytes, mime_type="image/png")

    mock_or.assert_called_once()
    mock_tess.assert_called_once()
    assert "Recovered via Tesseract from OpenRouter failure" in res


def test_both_primary_and_fallback_empty_raises_seed_extraction_error():
    img_bytes = _create_sample_image("PNG")

    custom_settings = Settings(
        vision=VisionConfig(
            extractor="gemini",
            gemini_api_key="valid-key",
        )
    )

    with patch("ingestion.extractors.image.get_settings", return_value=custom_settings):
        with patch(
            "ingestion.extractors.image.extract_text_with_gemini",
            return_value="",
        ):
            with patch(
                "ingestion.extractors.image.extract_text_with_tesseract",
                return_value="   ",
            ):
                with pytest.raises(SeedExtractionError, match="No readable text was found in this image"):
                    extract_text_from_image(img_bytes, mime_type="image/png")


def test_invalid_image_bytes_raises_error():
    with pytest.raises(SeedExtractionError, match="This file isn't a readable image"):
        extract_text_from_image(b"not-an-image-payload", mime_type="image/png")


def test_registry_integration_image_with_vision_and_chunker():
    reg = get_default_registry()

    img_bytes = _create_sample_image("PNG")

    custom_settings = Settings(
        vision=VisionConfig(
            extractor="gemini",
            gemini_api_key="mock-key",
            gemini_model="gemini-2.0-flash",
        )
    )

    vision_markdown = (
        "## Cloud Architecture Diagram\n"
        "High level architecture.\n\n"
        "1. Ingestion Pipeline\n"
        "- File upload -> Extractor\n"
        "- Extractor -> Chunking"
    )

    with patch("ingestion.extractors.image.get_settings", return_value=custom_settings):
        with patch("ingestion.extractors.image.extract_text_with_gemini", return_value=vision_markdown):
            extracted = reg.extract(img_bytes, filename="arch_diagram.png")

    assert "## Cloud Architecture Diagram" in extracted
    assert "## 1. Ingestion Pipeline" in extracted
    assert "- File upload -> Extractor" in extracted

    # Test StructureAwareChunker hierarchical parsing
    chunker = StructureAwareChunker(chunk_size=300)
    chunks = chunker.chunk(extracted)
    assert len(chunks) > 0
    assert any(
        "Cloud Architecture" in (c.section_path or "") or "Ingestion Pipeline" in (c.section_path or "") for c in chunks
    )
