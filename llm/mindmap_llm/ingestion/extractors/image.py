"""Multimodal vision model integration for image ingestion with Tesseract OCR fallback.

Integrates Google Gemini Flash and OpenRouter vision APIs to extract structured
text, hierarchy, and diagram labels directly from whiteboard photos, slides, and charts.
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Any

from config import get_settings
from ingestion.extractors.base import (
    SeedExtractionError,
    detect_structural_cues,
)
from tracing import trace_span

logger = logging.getLogger(__name__)

VISION_PROMPT = """You are an expert document and diagram transcription system.
Extract all structured text, hierarchy, titles, section headers, diagram labels, flowcharts, tables, and notes from this image.
Format the output into clean, structured Markdown:
- Use Markdown headings (## for sections, ### for sub-components, #### for deeper items).
- Use Markdown bullet points (- or 1.) for lists, nodes, and hierarchy items.
- For diagrams, flowcharts, or architectural sketches, describe relationships and connections clearly (e.g. Node A -> Node B).
- Transcribe all text faithfully without conversational intro or filler commentary.
"""


def sniff_image_mime(data: bytes) -> str:
    """Determine image MIME type from magic bytes."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data.startswith(b"BM"):
        return "image/bmp"
    if data.startswith(b"II*\x00") or data.startswith(b"MM\x00*"):
        return "image/tiff"
    return "image/png"


@trace_span("extract_text_with_tesseract", run_type="tool")
def extract_text_with_tesseract(data: bytes) -> str:
    """Extract text from image bytes using pytesseract OCR (explicit fallback)."""
    try:
        import pytesseract
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:  # pragma: no cover
        raise SeedExtractionError("pytesseract/Pillow not installed. OCR support unavailable.") from exc

    try:
        image = Image.open(io.BytesIO(data))
        image.verify()
        image = Image.open(io.BytesIO(data))
    except UnidentifiedImageError as exc:
        raise SeedExtractionError("This file isn't a readable image.") from exc
    except Exception as exc:
        raise SeedExtractionError(f"Couldn't open this image: {exc}") from exc

    try:
        text = pytesseract.image_to_string(image)
    except Exception as exc:
        raise SeedExtractionError(f"OCR failed on this image: {exc}") from exc

    return text.strip()


@trace_span("extract_text_with_gemini", run_type="llm")
def extract_text_with_gemini(
    data: bytes,
    mime_type: str,
    model: str,
    api_key: str,
    timeout_s: float = 20.0,
) -> str:
    """Extract structured text and diagrams using Google Gemini Flash vision model."""
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not configured.")

    import sys

    try:
        genai = sys.modules.get("google.genai")
        types = sys.modules.get("google.genai.types") or getattr(genai, "types", None)
        if genai is None or types is None:
            from google import genai
            from google.genai import types
        assert genai is not None and types is not None
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("google-genai not installed. Run `uv add google-genai`.") from exc

    client = genai.Client(api_key=api_key)
    part = types.Part.from_bytes(data=data, mime_type=mime_type)

    models_to_try = [model]
    for fallback in ["gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.8-flash"]:
        if fallback not in models_to_try:
            models_to_try.append(fallback)

    last_exc = None
    for target_model in models_to_try:
        try:
            response = client.models.generate_content(
                model=target_model,
                contents=[part, VISION_PROMPT],  # type: ignore[arg-type]
            )
            text = response.text or ""
            return text.strip()
        except Exception as exc:
            last_exc = exc
            logger.warning("Gemini model %s extraction failed (%s), trying next fallback...", target_model, exc)

    if last_exc:
        raise last_exc
    return ""


@trace_span("extract_text_with_openrouter", run_type="llm")
def extract_text_with_openrouter(
    data: bytes,
    mime_type: str,
    model: str,
    api_key: str,
    api_base: str = "https://openrouter.ai/api/v1",
    timeout_s: float = 20.0,
) -> str:
    """Extract structured text and diagrams using OpenRouter multimodal vision API."""
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is not configured.")

    import httpx

    b64_image = base64.b64encode(data).decode("utf-8")
    data_url = f"data:{mime_type};base64,{b64_image}"

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    url = f"{api_base.rstrip('/')}/chat/completions"
    with httpx.Client(timeout=timeout_s) as client:
        res = client.post(url, json=payload, headers=headers)
        res.raise_for_status()
        data_json = res.json()
        text = data_json["choices"][0]["message"]["content"]
        return str(text or "").strip()


@trace_span("extract_text_from_image", run_type="chain")
def extract_text_from_image(
    data: bytes, mime_type: str | None = None, *, extractor: str | None = None, **kwargs: Any
) -> str:
    """Extract structured text from image using Gemini Flash / OpenRouter vision with Tesseract fallback.

    Feature flag:
    - IMAGE_EXTRACTOR=gemini (default): Uses Gemini Flash (configurable via GEMINI_VISION_MODEL)
    - IMAGE_EXTRACTOR=openrouter: Uses OpenRouter vision model
    - IMAGE_EXTRACTOR=tesseract: Uses pytesseract OCR directly
    """
    try:
        from PIL import Image, UnidentifiedImageError

        image = Image.open(io.BytesIO(data))
        image.verify()
    except UnidentifiedImageError as exc:
        raise SeedExtractionError("This file isn't a readable image.") from exc
    except Exception as exc:
        raise SeedExtractionError(f"Couldn't open this image: {exc}") from exc

    resolved_mime = mime_type or sniff_image_mime(data)
    settings = get_settings()
    extractor_choice = (extractor or settings.vision.extractor or "gemini").lower()

    extracted_text = ""

    if extractor_choice == "gemini":
        try:
            res = extract_text_with_gemini(
                data=data,
                mime_type=resolved_mime,
                model=settings.vision.gemini_model,
                api_key=settings.vision.gemini_api_key,
                timeout_s=settings.vision.timeout_s,
            )
            extracted_text = (res or "").strip()
        except Exception as exc:
            logger.warning(
                "Gemini Flash vision extraction failed (%s), falling back to Tesseract OCR",
                exc,
            )

    elif extractor_choice == "openrouter":
        try:
            res = extract_text_with_openrouter(
                data=data,
                mime_type=resolved_mime,
                model=settings.vision.openrouter_model,
                api_key=settings.vision.openrouter_api_key,
                api_base=settings.vision.openrouter_api_base,
                timeout_s=settings.vision.timeout_s,
            )
            extracted_text = (res or "").strip()
        except Exception as exc:
            logger.warning(
                "OpenRouter vision extraction failed (%s), falling back to Tesseract OCR",
                exc,
            )

    # If primary vision extractor failed, returned empty text, or extractor_choice == "tesseract":
    if not extracted_text or not extracted_text.strip():
        try:
            res = extract_text_with_tesseract(data)
            extracted_text = (res or "").strip()
        except Exception as exc:
            logger.warning("Tesseract OCR fallback failed: %s", exc)

    if not extracted_text or not extracted_text.strip():
        raise SeedExtractionError(
            "No readable text was found in this image. If it contains a diagram or "
            "photo rather than text, describe it in the free-text field instead."
        )

    # Convert structural cues (ALL-CAPS headings, numbered lists, bullet points)
    return detect_structural_cues(extracted_text)
