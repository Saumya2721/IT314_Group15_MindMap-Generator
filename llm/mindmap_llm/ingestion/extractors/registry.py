"""Extensible extractor registry mapping MIME types and file extensions to parser callables.

Enforces per-type size limits, execution timeouts, fallback handlers, and
heading-marked Markdown output for StructureAwareChunker.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import logging
import os
from collections.abc import Callable
from typing import Any

from ingestion.extractors.base import (
    ExtractionTimeoutError,
    ExtractorSpec,
    FileSizeLimitExceededError,
    SeedExtractionError,
    UnsupportedMediaTypeError,
    detect_structural_cues,
)
from ingestion.extractors.docx import extract_text_from_docx
from ingestion.extractors.image import extract_text_from_image
from ingestion.extractors.pdf import extract_text_from_pdf
from ingestion.extractors.pptx import extract_text_from_pptx

logger = logging.getLogger(__name__)


def extract_plain_text(data: bytes, **kwargs: Any) -> str:
    """Extract plain text or Markdown bytes, decoding with fallback and normalizing structural cues."""
    for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", errors="replace")

    normalized = detect_structural_cues(text.strip())
    if not normalized:
        raise SeedExtractionError("Text file is empty or contains only unreadable characters.")
    return normalized


class ExtractorRegistry:
    """Central registry mapping MIME types and file extensions to extractor specifications."""

    def __init__(self) -> None:
        self._specs: dict[str, ExtractorSpec] = {}
        self._mime_map: dict[str, ExtractorSpec] = {}
        self._ext_map: dict[str, ExtractorSpec] = {}

    def register(self, spec: ExtractorSpec) -> None:
        """Register an ExtractorSpec."""
        self._specs[spec.name] = spec
        for mime in spec.mime_types:
            clean_mime = mime.strip().lower()
            self._mime_map[clean_mime] = spec

        for ext in spec.extensions:
            clean_ext = ext.strip().lower()
            if not clean_ext.startswith("."):
                clean_ext = f".{clean_ext}"
            self._ext_map[clean_ext] = spec

    def unregister(self, name: str) -> bool:
        """Unregister an extractor by name."""
        spec = self._specs.pop(name, None)
        if not spec:
            return False

        self._mime_map = {m: s for m, s in self._mime_map.items() if s.name != name}
        self._ext_map = {e: s for e, s in self._ext_map.items() if s.name != name}
        return True

    def get(self, mime_type: str | None = None, filename: str | None = None) -> ExtractorSpec | None:
        """Resolve extractor by MIME type (exact or wildcard) with fallback to file extension."""
        # 1. Exact MIME lookup
        if mime_type:
            clean_mime = mime_type.strip().lower()
            if clean_mime in self._mime_map:
                return self._mime_map[clean_mime]

            # Wildcard MIME lookup (e.g. image/*)
            if "/" in clean_mime:
                prefix = clean_mime.split("/")[0]
                wildcard = f"{prefix}/*"
                if wildcard in self._mime_map:
                    return self._mime_map[wildcard]

        # 2. File extension fallback lookup
        if filename:
            _, ext = os.path.splitext(filename)
            clean_ext = ext.strip().lower()
            if clean_ext and clean_ext in self._ext_map:
                return self._ext_map[clean_ext]

        return None

    def is_supported(self, mime_type: str | None = None, filename: str | None = None) -> bool:
        """Check whether a given MIME type or filename is supported by the registry."""
        return self.get(mime_type, filename) is not None

    def get_supported_mime_types(self) -> list[str]:
        """Derive all supported MIME types dynamically from registered keys."""
        return sorted({mime for spec in self._specs.values() for mime in spec.mime_types})

    def get_supported_extensions(self) -> list[str]:
        """Derive all supported file extensions dynamically from registered keys."""
        return sorted({ext for spec in self._specs.values() for ext in spec.extensions})

    def get_max_size(self, mime_type: str | None = None, filename: str | None = None) -> int:
        """Get max allowed size in bytes for a file type, defaulting to 25 MB."""
        spec = self.get(mime_type, filename)
        if spec:
            return spec.max_size_bytes
        return 25 * 1024 * 1024

    def get_upload_allowlist(self) -> dict[str, Any]:
        """Derive upload allowlist metadata dynamically from registered extractors."""
        return {
            "mime_types": self.get_supported_mime_types(),
            "extensions": self.get_supported_extensions(),
            "max_file_size_bytes": max(
                (s.max_size_bytes for s in self._specs.values()),
                default=25 * 1024 * 1024,
            ),
            "per_type_limits": {s.name: s.max_size_bytes for s in self._specs.values()},
        }

    def _execute_handler(
        self,
        handler: Callable[..., Any],
        data: bytes,
        kwargs: dict[str, Any],
    ) -> str:
        """Execute callable inspecting parameter support."""
        sig = inspect.signature(handler)
        if len(sig.parameters) == 1:
            return str(handler(data))
        return str(handler(data, **kwargs))

    def _run_handler_with_fallback(
        self,
        spec: ExtractorSpec,
        data: bytes,
        kwargs: dict[str, Any],
    ) -> str:
        """Invoke primary extractor handler; on failure, invoke fallback if defined."""
        try:
            result = self._execute_handler(spec.handler, data, kwargs)
            if result and result.strip():
                return result
            # Empty result returned by primary handler
            if spec.fallback_handler is not None:
                logger.warning("Primary handler for %s returned empty text, trying fallback", spec.name)
                fallback_result = self._execute_handler(spec.fallback_handler, data, kwargs)
                if fallback_result and fallback_result.strip():
                    return fallback_result
            raise SeedExtractionError(f"No readable text was extracted for {spec.name}.")
        except Exception as exc:
            if spec.fallback_handler is not None:
                logger.warning(
                    "Primary handler for %s failed with %s, invoking fallback handler",
                    spec.name,
                    exc,
                )
                try:
                    fallback_result = self._execute_handler(spec.fallback_handler, data, kwargs)
                    if fallback_result and fallback_result.strip():
                        return fallback_result
                except Exception as fb_exc:
                    raise SeedExtractionError(
                        f"Both primary and fallback extractors failed for {spec.name}: {fb_exc}"
                    ) from fb_exc
            if isinstance(exc, SeedExtractionError):
                raise
            raise SeedExtractionError(f"Extraction failed for {spec.name}: {exc}") from exc

    def extract(
        self,
        data: bytes,
        mime_type: str | None = None,
        filename: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Synchronously extract Markdown text with size and timeout guards."""
        text, _ = self.extract_with_spec(data, mime_type=mime_type, filename=filename, **kwargs)
        return text

    def extract_with_spec(
        self,
        data: bytes,
        mime_type: str | None = None,
        filename: str | None = None,
        **kwargs: Any,
    ) -> tuple[str, ExtractorSpec]:
        """Extract text and return both the formatted Markdown text and the matched ExtractorSpec."""
        if len(data) == 0:
            raise SeedExtractionError("The file is empty.")

        spec = self.get(mime_type, filename)
        if spec is None:
            raise UnsupportedMediaTypeError(f"Unsupported file type: {mime_type or filename}")

        if len(data) > spec.max_size_bytes:
            mb_limit = spec.max_size_bytes // (1024 * 1024)
            raise FileSizeLimitExceededError(
                f"File exceeds maximum allowed size of {mb_limit} MB for {spec.name} ({len(data)} bytes)."
            )

        # Enforce execution timeout via thread pool
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self._run_handler_with_fallback, spec, data, kwargs)
            try:
                raw_text = future.result(timeout=spec.timeout_seconds)
            except TimeoutError as exc:
                raise ExtractionTimeoutError(
                    f"Extraction timed out after {spec.timeout_seconds}s for {spec.name}."
                ) from exc

        # Format output for StructureAwareChunker: ensure markdown structural cues are present
        structured = detect_structural_cues(raw_text.strip())
        return structured, spec

    async def aextract(
        self,
        data: bytes,
        mime_type: str | None = None,
        filename: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Asynchronously extract Markdown text with size and timeout guards."""
        text, _ = await self.aextract_with_spec(data, mime_type=mime_type, filename=filename, **kwargs)
        return text

    async def aextract_with_spec(
        self,
        data: bytes,
        mime_type: str | None = None,
        filename: str | None = None,
        **kwargs: Any,
    ) -> tuple[str, ExtractorSpec]:
        """Asynchronously extract text and return both formatted text and ExtractorSpec."""
        if len(data) == 0:
            raise SeedExtractionError("The file is empty.")

        spec = self.get(mime_type, filename)
        if spec is None:
            raise UnsupportedMediaTypeError(f"Unsupported file type: {mime_type or filename}")

        if len(data) > spec.max_size_bytes:
            mb_limit = spec.max_size_bytes // (1024 * 1024)
            raise FileSizeLimitExceededError(
                f"File exceeds maximum allowed size of {mb_limit} MB for {spec.name} ({len(data)} bytes)."
            )

        # Run handler in thread or as coroutine with asyncio.wait_for timeout
        async def _run() -> str:
            if inspect.iscoroutinefunction(spec.handler):
                return await spec.handler(data, **kwargs)
            return await asyncio.to_thread(self._run_handler_with_fallback, spec, data, kwargs)

        try:
            raw_text = await asyncio.wait_for(_run(), timeout=spec.timeout_seconds)
        except TimeoutError as exc:
            raise ExtractionTimeoutError(
                f"Extraction timed out after {spec.timeout_seconds}s for {spec.name}."
            ) from exc

        structured = detect_structural_cues(raw_text.strip())
        return structured, spec


_default_registry: ExtractorRegistry | None = None


def get_default_registry() -> ExtractorRegistry:
    """Retrieve or initialize the global default ExtractorRegistry."""
    global _default_registry
    if _default_registry is None:
        registry = ExtractorRegistry()

        # Register PDF Extractor
        registry.register(
            ExtractorSpec(
                name="pdf",
                mime_types=["application/pdf"],
                extensions=[".pdf"],
                handler=extract_text_from_pdf,
                max_size_bytes=25 * 1024 * 1024,
                timeout_seconds=30.0,
                asset_type="pdf",
                description="Extracts headings, TOC bookmarks and pages from PDF files.",
            )
        )

        # Register Image Extractor (OCR)
        registry.register(
            ExtractorSpec(
                name="image",
                mime_types=[
                    "image/png",
                    "image/jpeg",
                    "image/jpg",
                    "image/webp",
                    "image/bmp",
                    "image/tiff",
                    "image/gif",
                ],
                extensions=[".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".gif"],
                handler=extract_text_from_image,
                max_size_bytes=20 * 1024 * 1024,
                timeout_seconds=20.0,
                asset_type="image",
                description="Extracts text and diagrams from image uploads via OCR.",
            )
        )

        # Register Plain Text / Markdown Extractor
        registry.register(
            ExtractorSpec(
                name="text",
                mime_types=["text/plain", "text/markdown"],
                extensions=[".txt", ".md", ".markdown"],
                handler=extract_plain_text,
                max_size_bytes=10 * 1024 * 1024,
                timeout_seconds=10.0,
                asset_type="text",
                description="Normalizes structural cues from plain text and Markdown files.",
            )
        )

        # Register Word (.docx) Extractor
        registry.register(
            ExtractorSpec(
                name="docx",
                mime_types=[
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "application/msword",
                ],
                extensions=[".docx", ".doc"],
                handler=extract_text_from_docx,
                max_size_bytes=25 * 1024 * 1024,
                timeout_seconds=30.0,
                asset_type="text",
                description="Extracts Word document headings and paragraphs into Markdown.",
            )
        )

        # Register PowerPoint (.pptx) Extractor
        registry.register(
            ExtractorSpec(
                name="pptx",
                mime_types=[
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    "application/vnd.ms-powerpoint",
                ],
                extensions=[".pptx", ".ppt"],
                handler=extract_text_from_pptx,
                max_size_bytes=25 * 1024 * 1024,
                timeout_seconds=30.0,
                asset_type="text",
                description="Extracts PowerPoint slide titles into headings and body bullets into text.",
            )
        )

        _default_registry = registry
    return _default_registry


def reset_default_registry() -> None:
    """Reset the global default registry singleton (primarily for testing)."""
    global _default_registry
    _default_registry = None
