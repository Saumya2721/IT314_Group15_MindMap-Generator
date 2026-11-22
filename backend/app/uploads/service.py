"""Upload validation and Supabase Storage operations.

Extracted cleanly from mm_backend into backend.uploads.service.
"""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import logging
import os
import re
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from contracts.models import ALLOWED_PURPOSES, DEFAULT_PURPOSE
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
MAX_OFFICE_FILE_BYTES = 20 * 1024 * 1024  # 20 MB limit for DOCX/PPTX (S3-BE7)
MAX_SEED_URL_LENGTH = 2000  # S3-BE7: max URL length

# SSRF protection: private/loopback IP ranges that seed URLs must NOT resolve to (S3-BE7)
_PRIVATE_NETS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),  # unique-local IPv6
    ipaddress.ip_network("fe80::/10"),  # link-local IPv6
]


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


def sniff_mime(data: bytes, filename: str = "") -> str | None:
    """Decide the type from the file's own magic bytes, never client-supplied headers.

    For ZIP-based Office formats (DOCX/PPTX) the magic bytes are identical
    (PK\\x03\\x04), so the filename extension is used as the tiebreaker.
    """
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    # S3-BE7: ZIP-based Office Open XML formats — same PK magic, distinguish by extension
    if data[:4] == b"PK\x03\x04":
        ext = os.path.splitext(filename.lower())[1]
        if ext == ".docx":
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if ext == ".pptx":
            return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
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
        # S3-BE7: office docs have a tighter size cap; read up to the stricter limit first
        is_office_ext = os.path.splitext(name.lower())[1] in (".docx", ".pptx")
        read_limit = (MAX_OFFICE_FILE_BYTES if is_office_ext else MAX_FILE_BYTES) + 1
        data = await f.read(read_limit)
        if not data:
            raise HTTPException(422, f"{name}: the file is empty.")
        if is_office_ext and len(data) > MAX_OFFICE_FILE_BYTES:
            raise HTTPException(413, f"{name}: file is larger than {MAX_OFFICE_FILE_BYTES // (1024 * 1024)} MB.")
        if not is_office_ext and len(data) > MAX_FILE_BYTES:
            raise HTTPException(413, f"{name}: file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")
        mime = sniff_mime(data, filename=name)
        if mime is None:
            raise HTTPException(415, f"{name}: unsupported or corrupted file. Use PDF, PNG, JPEG, WEBP, DOCX or PPTX.")
        items.append((name, mime, data))
    return items


def validate_seed_url(url: str) -> tuple[bool, str | None]:
    """Validate that *url* is a safe, public HTTP/HTTPS address (SSRF protection).

    Returns ``(True, None)`` when valid, or ``(False, reason)`` when invalid.
    Rejects: non-http(s) schemes, loopback, link-local, and all RFC-1918
    private address ranges, and URLs longer than MAX_SEED_URL_LENGTH.
    """
    if len(url) > MAX_SEED_URL_LENGTH:
        return False, f"URL is too long (max {MAX_SEED_URL_LENGTH} characters)."
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, "URL must start with http:// or https://."
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return False, "URL must have a valid hostname."
    if hostname in ("localhost", "0.0.0.0"):
        return False, "URL must point to a public address, not localhost."
    # Try to parse as IP — if it's an IP, check for private ranges
    try:
        addr = ipaddress.ip_address(hostname)
        for net in _PRIVATE_NETS:
            if addr in net:
                return False, "URL must point to a public address, not a private or loopback IP."
    except ValueError:
        pass  # hostname is a domain name, not a bare IP — allow it
    return True, None


def validate_purpose(purpose: str | None) -> tuple[bool, str, str | None]:
    """Validate and normalize the generation purpose (S3-FE5 / S3-BE7).

    Returns (is_valid, normalized_purpose, error_message).
    """
    if not purpose or not purpose.strip():
        return True, DEFAULT_PURPOSE, None

    cleaned = purpose.strip().lower()
    if cleaned not in ALLOWED_PURPOSES:
        allowed_list = ", ".join(sorted(ALLOWED_PURPOSES))
        return False, cleaned, f"Invalid purpose '{purpose}'. Allowed values: {allowed_list}."

    return True, cleaned, None


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
                SELECT sa.storage_key
                FROM public.seed_assets sa
                JOIN public.generation_jobs gj ON sa.generation_job_id = gj.id
                WHERE gj.map_id = $1 AND sa.storage_key IS NOT NULL
                """,
                map_id,
            )
            for r in rows:
                key = r.get("storage_key") or r.get("storage_path")
                if key:
                    keys_to_remove.add(key)
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
            raise RuntimeError(f"Storage deletion failed for keys {keys_list}: {exc}") from exc

    return []


async def delete_user_storage_files(
    user_id: UUID,
    pool: Any = None,
) -> list[str]:
    """Remove all Supabase Storage files associated with a user across all their maps.

    Cleans up files recorded in the database (public.seed_assets) and any
    files stored under the storage prefix `<user_id>/`.
    """
    keys_to_remove: set[str] = set()

    # 1. Fetch explicitly recorded storage paths from DB if pool is available
    if pool is not None:
        try:
            rows = await pool.fetch(
                """
                SELECT sa.storage_key
                FROM public.seed_assets sa
                JOIN public.generation_jobs gj ON sa.generation_job_id = gj.id
                JOIN public.mind_maps m ON gj.map_id = m.id
                WHERE m.user_id = $1 AND sa.storage_key IS NOT NULL
                """,
                user_id,
            )
            for r in rows:
                key = r.get("storage_key") or r.get("storage_path")
                if key:
                    keys_to_remove.add(key)
        except Exception as exc:
            logger.warning("Could not query seed_assets for user %s: %s", user_id, exc)

    # 2. Discover any objects under the folder prefix <user_id>/
    try:
        prefix = str(user_id)
        top_items = await asyncio.to_thread(admin.storage.from_(BUCKET).list, prefix)
        if isinstance(top_items, list):
            for item in top_items:
                name = item.get("name") if isinstance(item, dict) else getattr(item, "name", None)
                if not name:
                    continue
                sub_path = f"{prefix}/{name}"
                try:
                    sub_items = await asyncio.to_thread(admin.storage.from_(BUCKET).list, sub_path)
                    if isinstance(sub_items, list) and sub_items:
                        for sub in sub_items:
                            sub_name = sub.get("name") if isinstance(sub, dict) else getattr(sub, "name", None)
                            if not sub_name:
                                continue
                            leaf_path = f"{sub_path}/{sub_name}"
                            try:
                                leaf_items = await asyncio.to_thread(admin.storage.from_(BUCKET).list, leaf_path)
                                if isinstance(leaf_items, list) and leaf_items:
                                    for leaf in leaf_items:
                                        leaf_name = (
                                            leaf.get("name") if isinstance(leaf, dict) else getattr(leaf, "name", None)
                                        )
                                        if leaf_name:
                                            keys_to_remove.add(f"{leaf_path}/{leaf_name}")
                                else:
                                    keys_to_remove.add(leaf_path)
                            except Exception:
                                keys_to_remove.add(leaf_path)
                    else:
                        keys_to_remove.add(sub_path)
                except Exception:
                    keys_to_remove.add(sub_path)
    except Exception as exc:
        logger.warning("Could not list storage prefix %s: %s", user_id, exc)

    if keys_to_remove:
        keys_list = list(keys_to_remove)
        try:
            await asyncio.to_thread(admin.storage.from_(BUCKET).remove, keys_list)
            logger.info("Successfully deleted %d storage files for user %s", len(keys_list), user_id)
            return keys_list
        except Exception as exc:
            logger.warning("Failed to remove storage keys %s for user %s: %s", keys_list, user_id, exc)
            raise RuntimeError(f"Storage deletion failed for keys {keys_list}: {exc}") from exc

    return []
