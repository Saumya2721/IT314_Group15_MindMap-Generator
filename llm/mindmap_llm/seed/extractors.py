"""
Per-type content extraction. Kept as separate functions per DR/US-02
technical note: "Parsing should remain distinct for each input type."
Each extractor returns plain text (or raises SeedExtractionError) —
callers never need to know whether a PDF was OCR'd or text-parsed.
"""

from __future__ import annotations

import hashlib
import io


class SeedExtractionError(Exception):
    """Raised for a malformed, corrupt, or unreadable upload. Caught by
    seed/normalizer.py and turned into a Scenario-4-style clear message —
    never propagates as a raw parser traceback to the user."""


def checksum_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_text_from_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pypdf not installed. `pip install pypdf`.") from exc

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise SeedExtractionError("This PDF is password-protected and can't be read.")
        pages_text = [page.extract_text() or "" for page in reader.pages]
    except SeedExtractionError:
        raise
    except Exception as exc:
        raise SeedExtractionError(f"Couldn't parse this PDF: {exc}") from exc

    text = "\n\n".join(t for t in pages_text if t.strip())
    if not text.strip():
        # No extractable text layer — a scanned PDF. Sprint 1 does not include
        # OCR; flag it clearly rather than silently returning an empty seed.
        raise SeedExtractionError(
            "This PDF has no extractable text (it may be a scanned image). "
            "OCR support is not yet enabled — try uploading it as an image instead, "
            "or a text-based PDF."
        )
    return text


def extract_text_from_image(data: bytes) -> str:
    try:
        import pytesseract
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "pytesseract/Pillow not installed. `pip install pytesseract pillow`."
        ) from exc

    try:
        image = Image.open(io.BytesIO(data))
        image.verify()
        image = Image.open(io.BytesIO(data))  # re-open: verify() consumes the file pointer
    except UnidentifiedImageError as exc:
        raise SeedExtractionError("This file isn't a readable image.") from exc
    except Exception as exc:
        raise SeedExtractionError(f"Couldn't open this image: {exc}") from exc

    try:
        text: str = pytesseract.image_to_string(image)
    except Exception as exc:
        raise SeedExtractionError(f"OCR failed on this image: {exc}") from exc

    if not text.strip():
        raise SeedExtractionError(
            "No readable text was found in this image. If it contains a diagram or "
            "photo rather than text, describe it in the free-text field instead."
        )
    return text.strip()


def normalize_free_text(topic: str | None, ideas: str | None) -> str:
    parts = [p.strip() for p in (topic, ideas) if p and p.strip()]
    if not parts:
        raise SeedExtractionError("No topic or free-text ideas were provided.")
    return "\n\n".join(parts)
