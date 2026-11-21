"""Unit tests for S3-BE7 — Contracts & Intake for new sources.

Covers:
- SeedAssetType / ChunkSource enum extensions
- GenerationRequest seed_url field (backend contracts)
- sniff_mime() DOCX / PPTX support
- read_uploads() DOCX / PPTX size limits and error messages
- validate_seed_url() SSRF protection
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock

import pytest
from contracts.models import ChunkSource, GenerationRequest, JobType, SeedAssetType
from fastapi import UploadFile
from uploads.service import sniff_mime, validate_seed_url

# ---------------------------------------------------------------------------
# Enum extensions
# ---------------------------------------------------------------------------


class TestSeedAssetTypeExtensions:
    def test_docx_value(self) -> None:
        assert SeedAssetType.DOCX == "docx"

    def test_pptx_value(self) -> None:
        assert SeedAssetType.PPTX == "pptx"

    def test_url_value(self) -> None:
        assert SeedAssetType.URL == "url"

    def test_existing_values_unchanged(self) -> None:
        assert SeedAssetType.PDF == "pdf"
        assert SeedAssetType.IMAGE == "image"
        assert SeedAssetType.TEXT == "text"


class TestChunkSourceExtensions:
    def test_wikipedia_value(self) -> None:
        assert ChunkSource.WIKIPEDIA == "wikipedia"

    def test_scholar_value(self) -> None:
        assert ChunkSource.SCHOLAR == "scholar"

    def test_page_value(self) -> None:
        assert ChunkSource.PAGE == "page"

    def test_existing_values_unchanged(self) -> None:
        assert ChunkSource.UPLOADED_SEED == "uploaded_seed"
        assert ChunkSource.WEB_SEARCH == "web_search"
        assert ChunkSource.ARXIV == "arxiv"


# ---------------------------------------------------------------------------
# GenerationRequest seed_url field
# ---------------------------------------------------------------------------


class TestGenerationRequestSeedUrl:
    _base = dict(
        job_id="00000000-0000-0000-0000-000000000001",
        map_id="00000000-0000-0000-0000-000000000002",
        job_type=JobType.GENERATE,
        callback_base_url="http://localhost",
        callback_token="tok",
    )

    def test_seed_url_defaults_to_none(self) -> None:
        req = GenerationRequest(**self._base)
        assert req.seed_url is None

    def test_seed_url_accepts_valid_url(self) -> None:
        req = GenerationRequest(**self._base, seed_url="https://en.wikipedia.org/wiki/Python")
        assert req.seed_url == "https://en.wikipedia.org/wiki/Python"

    def test_seed_url_accepts_none_explicitly(self) -> None:
        req = GenerationRequest(**self._base, seed_url=None)
        assert req.seed_url is None


# ---------------------------------------------------------------------------
# sniff_mime — DOCX / PPTX
# ---------------------------------------------------------------------------

# Minimal ZIP magic header (PK\x03\x04)
_ZIP_MAGIC = b"PK\x03\x04" + b"\x00" * 26  # 30 bytes — local file header


class TestSniffMimeOffice:
    def test_docx_detected(self) -> None:
        mime = sniff_mime(_ZIP_MAGIC, filename="report.docx")
        assert mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    def test_pptx_detected(self) -> None:
        mime = sniff_mime(_ZIP_MAGIC, filename="slides.pptx")
        assert mime == "application/vnd.openxmlformats-officedocument.presentationml.presentation"

    def test_unknown_zip_ext_returns_none(self) -> None:
        # ZIP with unsupported extension (e.g. .xlsx) must be rejected
        mime = sniff_mime(_ZIP_MAGIC, filename="data.xlsx")
        assert mime is None

    def test_pdf_still_works(self) -> None:
        mime = sniff_mime(b"%PDF-1.5\n", filename="doc.pdf")
        assert mime == "application/pdf"

    def test_png_still_works(self) -> None:
        mime = sniff_mime(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10, filename="img.png")
        assert mime == "image/png"

    def test_jpeg_still_works(self) -> None:
        mime = sniff_mime(b"\xff\xd8\xff\xe0" + b"\x00" * 10, filename="photo.jpg")
        assert mime == "image/jpeg"

    def test_webp_still_works(self) -> None:
        mime = sniff_mime(b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 10, filename="img.webp")
        assert mime == "image/webp"

    def test_garbage_returns_none(self) -> None:
        mime = sniff_mime(b"\x00\x01\x02\x03garbage", filename="bad.bin")
        assert mime is None


# ---------------------------------------------------------------------------
# read_uploads — office size limit
# ---------------------------------------------------------------------------


def _make_upload(data: bytes, filename: str) -> UploadFile:
    """Create a mock UploadFile that reads from *data*."""
    buf = io.BytesIO(data)
    uf = UploadFile(filename=filename, file=buf)  # type: ignore[call-arg]
    uf.read = AsyncMock(return_value=data)  # type: ignore[method-assign]
    return uf


class TestReadUploadsOffice:
    @pytest.mark.asyncio
    async def test_valid_docx_accepted(self) -> None:
        from uploads.service import read_uploads

        data = _ZIP_MAGIC  # tiny but magic-bytes correct
        uf = _make_upload(data, "report.docx")
        result = await read_uploads([uf])
        assert len(result) == 1
        name, mime, raw = result[0]
        assert mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        assert name == "report.docx"

    @pytest.mark.asyncio
    async def test_valid_pptx_accepted(self) -> None:
        from uploads.service import read_uploads

        data = _ZIP_MAGIC
        uf = _make_upload(data, "slides.pptx")
        result = await read_uploads([uf])
        assert result[0][1] == "application/vnd.openxmlformats-officedocument.presentationml.presentation"

    @pytest.mark.asyncio
    async def test_oversized_docx_rejected(self) -> None:
        from fastapi import HTTPException
        from uploads.service import MAX_OFFICE_FILE_BYTES, read_uploads

        # Simulate data 1 byte over the office limit
        data = b"PK\x03\x04" + b"\x00" * (MAX_OFFICE_FILE_BYTES - 2)
        uf = _make_upload(data, "big.docx")
        with pytest.raises(HTTPException) as exc_info:
            await read_uploads([uf])
        assert exc_info.value.status_code == 413

    @pytest.mark.asyncio
    async def test_unsupported_format_rejected(self) -> None:
        from fastapi import HTTPException
        from uploads.service import read_uploads

        data = b"\x00\x01\x02\x03garbage"
        uf = _make_upload(data, "bad.bin")
        with pytest.raises(HTTPException) as exc_info:
            await read_uploads([uf])
        assert exc_info.value.status_code == 415
        assert "DOCX or PPTX" in exc_info.value.detail


# ---------------------------------------------------------------------------
# validate_seed_url
# ---------------------------------------------------------------------------


class TestValidateSeedUrl:
    # --- valid cases ---
    def test_valid_https_url(self) -> None:
        ok, err = validate_seed_url("https://en.wikipedia.org/wiki/Python_(programming_language)")
        assert ok is True
        assert err is None

    def test_valid_http_url(self) -> None:
        ok, err = validate_seed_url("http://example.com/article")
        assert ok is True

    def test_valid_url_with_path_and_query(self) -> None:
        ok, err = validate_seed_url("https://scholar.google.com/scholar?q=neural+networks")
        assert ok is True

    # --- scheme errors ---
    def test_ftp_scheme_rejected(self) -> None:
        ok, err = validate_seed_url("ftp://files.example.com/data.csv")
        assert ok is False
        assert err is not None
        assert "http" in err.lower()

    def test_no_scheme_rejected(self) -> None:
        ok, err = validate_seed_url("example.com/page")
        assert ok is False

    # --- private / loopback IPs ---
    def test_localhost_rejected(self) -> None:
        ok, err = validate_seed_url("http://localhost/api/secret")
        assert ok is False
        assert err is not None

    def test_loopback_ip_rejected(self) -> None:
        ok, err = validate_seed_url("http://127.0.0.1/admin")
        assert ok is False

    def test_private_10_rejected(self) -> None:
        ok, err = validate_seed_url("http://10.0.0.1/internal")
        assert ok is False

    def test_private_192_168_rejected(self) -> None:
        ok, err = validate_seed_url("http://192.168.1.100/data")
        assert ok is False

    def test_private_172_16_rejected(self) -> None:
        ok, err = validate_seed_url("http://172.16.5.1/secret")
        assert ok is False

    def test_link_local_rejected(self) -> None:
        ok, err = validate_seed_url("http://169.254.169.254/latest/meta-data/")
        assert ok is False

    def test_zero_host_rejected(self) -> None:
        ok, err = validate_seed_url("http://0.0.0.0/hack")
        assert ok is False

    def test_ipv6_loopback_rejected(self) -> None:
        ok, err = validate_seed_url("http://[::1]/admin")
        assert ok is False

    # --- length limit ---
    def test_too_long_url_rejected(self) -> None:
        from uploads.service import MAX_SEED_URL_LENGTH

        url = "https://example.com/" + "a" * MAX_SEED_URL_LENGTH
        ok, err = validate_seed_url(url)
        assert ok is False
        assert err is not None
        assert "long" in err.lower()

    def test_exactly_max_length_url_accepted(self) -> None:
        from uploads.service import MAX_SEED_URL_LENGTH

        # Build a URL exactly at the limit that is structurally valid
        base = "https://example.com/"
        path = "a" * (MAX_SEED_URL_LENGTH - len(base))
        url = base + path
        assert len(url) == MAX_SEED_URL_LENGTH
        ok, _err = validate_seed_url(url)
        assert ok is True
