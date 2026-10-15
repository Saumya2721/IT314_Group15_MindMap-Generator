"""Upload validation and Supabase Storage operations.

Extracted cleanly from mm_backend into backend.uploads.service.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
from typing import List, Tuple
from uuid import UUID

from fastapi import HTTPException, UploadFile

from core.supabase import admin

try:
    from seed.normalizer import UploadedFile
except ImportError:
    from dataclasses import dataclass

    @dataclass
    class UploadedFile:
        file_name: str
        mime_type: str
        data: bytes
        storage_key: str | None = None
        signed_url: str | None = None

logger = logging.getLogger(__name__)

BUCKET = "seed-assets"
MAX_FILES = 5
MAX_FILE_BYTES = 25 * 1024 * 1024  # 25 MB limit


async def create_signed_url(key: str, expires_in: int = 1800) -> str:
    """Generate a short-lived signed URL for a file in Supabase Storage."""
    try:
        res = await asyncio.to_thread(
            admin.storage.from_(BUCKET).create_signed_url,
            key,
            expires_in,
        )
        url = getattr(res, "signedURL", None) or getattr(res, "signedUrl", None)
        if not url and isinstance(res, dict):
            url = res.get("signedURL") or res.get("signedUrl")
        return url or ""
    except Exception as exc:
        logger.warning("Could not generate signed URL for %s: %s", key, exc)
        return ""


def sniff_mime(data: bytes) -> str | None:
    """Decide the type from the file's own magic bytes, never client-supplied headers."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def display_name(name: str) -> str:
    base = os.path.basename((name or "file").replace("\\", "/")).strip()
    return (base or "file")[-200:]


def storage_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", display_name(name)).strip("._")
    return (safe or "file")[-100:]


async def read_uploads(files: List[UploadFile]) -> List[Tuple[str, str, bytes]]:
    """Fast-fail checks: count, empty, too large, unsupported/corrupt header."""
    files = [f for f in files if f is not None and f.filename]
    if len(files) > MAX_FILES:
        raise HTTPException(422, f"You can upload at most {MAX_FILES} files at a time.")
    items = []
    for f in files:
        name = display_name(f.filename)
        data = await f.read(MAX_FILE_BYTES + 1)  # never load more than the limit
        if not data:
            raise HTTPException(422, f"{name}: the file is empty.")
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(413, f"{name}: file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")
        mime = sniff_mime(data)
        if mime is None:
            raise HTTPException(415, f"{name}: unsupported or corrupted file. Use PDF, PNG, JPEG or WEBP.")
        items.append((name, mime, data))
    return items


async def store_files(
    user_id: UUID,
    map_id: UUID,
    job_id: UUID,
    items: List[Tuple[str, str, bytes]],
) -> List[UploadedFile]:
    """Upload to <user_id>/<map_id>/<job_id>/<n>_<file> in Supabase Storage and produce signed URLs."""
    result: List[UploadedFile] = []
    uploaded_keys: List[str] = []
    params = inspect.signature(UploadedFile).parameters
    has_storage_key_param = "storage_key" in params
    has_signed_url_param = "signed_url" in params

    for i, (name, mime, data) in enumerate(items):
        key = f"{user_id}/{map_id}/{job_id}/{i}_{storage_name(name)}"
        try:
            await asyncio.to_thread(
                admin.storage.from_(BUCKET).upload,
                key,
                data,
                {"content-type": mime, "upsert": "false"},
            )
            uploaded_keys.append(key)
        except Exception as exc:
            logger.error("Failed to upload file %s to Supabase Storage: %s", key, exc)
            if uploaded_keys:
                try:
                    await asyncio.to_thread(admin.storage.from_(BUCKET).remove, uploaded_keys)
                except Exception as rm_exc:
                    logger.warning("Failed to clean up partial storage keys %s: %s", uploaded_keys, rm_exc)
            raise HTTPException(502, "Could not store the uploaded file in Supabase Storage. Please try again.")

        signed_url = await create_signed_url(key)
        kwargs = {}
        if has_storage_key_param:
            kwargs["storage_key"] = key
        if has_signed_url_param:
            kwargs["signed_url"] = signed_url

        result.append(UploadedFile(file_name=name, mime_type=mime, data=data, **kwargs))

    return result
