"""Upload validation and Supabase Storage operations.

Extracted cleanly from mm_backend into backend.uploads.service.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
from typing import Any
from uuid import UUID

from core.supabase import admin
from fastapi import HTTPException, UploadFile

try:
    from seed.normalizer import UploadedFile
except ImportError:
    from dataclasses import dataclass

    @dataclass
    class _UploadedFileFallback:
        file_name: str
        mime_type: str
        data: bytes
        storage_key: str | None = None
        signed_url: str | None = None

    UploadedFile = _UploadedFileFallback  # type: ignore[misc]

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


async def read_uploads(files: list[UploadFile]) -> list[tuple[str, str, bytes]]:
    """Fast-fail checks: count, empty, too large, unsupported/corrupt header."""
    files = [f for f in files if f is not None and f.filename]
    if len(files) > MAX_FILES:
        raise HTTPException(422, f"You can upload at most {MAX_FILES} files at a time.")
    items = []
    for f in files:
        name = display_name(f.filename or "file")
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
    items: list[tuple[str, str, bytes]],
) -> list[UploadedFile]:
    """Upload to <user_id>/<map_id>/<job_id>/<n>_<file> in Supabase Storage and produce signed URLs."""
    result: list[UploadedFile] = []
    uploaded_keys: list[str] = []
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
        if not signed_url:
            # Signed URL creation failed — roll back all uploaded files including this one
            all_keys = uploaded_keys + [key]
            try:
                await asyncio.to_thread(admin.storage.from_(BUCKET).remove, all_keys)
            except Exception as rm_exc:
                logger.warning("Failed to clean up storage keys after signed URL failure %s: %s", all_keys, rm_exc)
            raise HTTPException(
                502,
                "Could not generate a download URL for the uploaded file. Please try again.",
            )
        kwargs = {}
        if has_storage_key_param:
            kwargs["storage_key"] = key
        if has_signed_url_param:
            kwargs["signed_url"] = signed_url

        result.append(UploadedFile(file_name=name, mime_type=mime, data=data, **kwargs))

    return result


async def delete_map_storage_files(
    user_id: UUID,
    map_id: UUID,
    pool: Any = None,
) -> list[str]:
    """Remove all Supabase Storage files associated with a map.

    Cleans up files recorded in the database (public.seed_assets) and any
    files stored under the storage prefix `<user_id>/<map_id>/`.
    """
    keys_to_remove: set[str] = set()

    # 1. Fetch explicitly recorded storage paths from DB if pool is available
    if pool is not None:
        try:
            rows = await pool.fetch(
                """
                SELECT sa.storage_path
                FROM public.seed_assets sa
                JOIN public.generation_jobs gj ON sa.generation_job_id = gj.id
                WHERE gj.map_id = $1 AND sa.storage_path IS NOT NULL
                """,
                map_id,
            )
            for r in rows:
                if r["storage_path"]:
                    keys_to_remove.add(r["storage_path"])
        except Exception as exc:
            logger.warning("Could not query seed_assets for map %s: %s", map_id, exc)

    # 2. Discover any objects under the folder prefix <user_id>/<map_id>/
    try:
        prefix = f"{user_id}/{map_id}"
        top_items = await asyncio.to_thread(admin.storage.from_(BUCKET).list, prefix)
        if isinstance(top_items, list):
            for item in top_items:
                name = item.get("name") if isinstance(item, dict) else getattr(item, "name", None)
                if not name:
                    continue
                sub_path = f"{prefix}/{name}"
                # If name looks like a UUID or folder, inspect nested files
                try:
                    sub_items = await asyncio.to_thread(admin.storage.from_(BUCKET).list, sub_path)
                    if isinstance(sub_items, list) and sub_items:
                        for sub in sub_items:
                            sub_name = sub.get("name") if isinstance(sub, dict) else getattr(sub, "name", None)
                            if sub_name:
                                keys_to_remove.add(f"{sub_path}/{sub_name}")
                    else:
                        keys_to_remove.add(sub_path)
                except Exception:
                    keys_to_remove.add(sub_path)
    except Exception as exc:
        logger.warning("Could not list storage prefix %s/%s: %s", user_id, map_id, exc)

    if keys_to_remove:
        keys_list = list(keys_to_remove)
        try:
            await asyncio.to_thread(admin.storage.from_(BUCKET).remove, keys_list)
            logger.info("Successfully deleted %d storage files for map %s", len(keys_list), map_id)
            return keys_list
        except Exception as exc:
            logger.warning("Failed to remove storage keys %s for map %s: %s", keys_list, map_id, exc)

    return []
