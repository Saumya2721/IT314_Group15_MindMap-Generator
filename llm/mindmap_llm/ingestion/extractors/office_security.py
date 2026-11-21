"""Security guards for Office Open XML (.docx, .pptx) files.

Enforces zip-bomb decompression limits and macro rejection.
"""

from __future__ import annotations

import io
import logging
import zipfile

from ingestion.extractors.base import SeedExtractionError

logger = logging.getLogger(__name__)

# Security defaults
DEFAULT_MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024  # 50 MB uncompressed cap
DEFAULT_MAX_COMPRESSION_RATIO = 50.0  # 50:1 ratio cap
DEFAULT_MAX_ARCHIVE_ENTRIES = 2000

MACRO_CONTENT_TYPES = {
    "application/vnd.ms-office.vbaproject",
    "application/vnd.ms-word.document.macroenabled.main+xml",
    "application/vnd.ms-word.template.macroenabled.template+xml",
    "application/vnd.ms-powerpoint.presentation.macroenabled.main+xml",
    "application/vnd.ms-powerpoint.slideshow.macroenabled.main+xml",
    "application/vnd.ms-powerpoint.template.macroenabled.template+xml",
}


def validate_office_archive(
    data: bytes,
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
    max_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO,
    max_entries: int = DEFAULT_MAX_ARCHIVE_ENTRIES,
) -> None:
    """Validate archive integrity, zip-bomb decompression thresholds, and reject macro payloads."""
    if not data:
        raise SeedExtractionError("The file is empty.")

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise SeedExtractionError(f"Corrupt or invalid document archive: {exc}") from exc

    entries = zf.infolist()
    if len(entries) > max_entries:
        raise SeedExtractionError(f"Document contains too many archive entries ({len(entries)} > {max_entries}).")

    total_uncompressed = 0
    for info in entries:
        total_uncompressed += info.file_size
        filename_lower = info.filename.lower()

        # Check for VBA project binary files and macro directories
        if (
            filename_lower.endswith("vbaproject.bin")
            or "vba" in filename_lower.split("/")
            or filename_lower.endswith("vbadat.xml")
            or filename_lower.endswith("vbadat")
        ):
            raise SeedExtractionError("Files containing macros are not permitted for security reasons.")

    # Enforce total decompression limit
    if total_uncompressed > max_uncompressed_bytes:
        limit_mb = max_uncompressed_bytes // (1024 * 1024)
        actual_mb = total_uncompressed // (1024 * 1024)
        raise SeedExtractionError(f"Document uncompressed size ({actual_mb} MB) exceeds safety limit of {limit_mb} MB.")

    # Enforce compression ratio guard
    if len(data) > 0 and total_uncompressed > 5 * 1024 * 1024:
        ratio = total_uncompressed / len(data)
        if ratio > max_ratio:
            raise SeedExtractionError(
                f"Document compression ratio ({ratio:.1f}x) exceeds safety threshold (possible zip-bomb)."
            )

    # Inspect [Content_Types].xml for declared macro types if present
    try:
        if "[Content_Types].xml" in zf.namelist():
            content_types_xml = zf.read("[Content_Types].xml").decode("utf-8", errors="ignore").lower()
            for macro_type in MACRO_CONTENT_TYPES:
                if macro_type in content_types_xml:
                    raise SeedExtractionError("Files containing macros are not permitted for security reasons.")
    except SeedExtractionError:
        raise
    except Exception as exc:
        logger.debug("Could not parse [Content_Types].xml: %s", exc)
