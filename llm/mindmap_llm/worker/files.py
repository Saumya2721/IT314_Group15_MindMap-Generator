"""
The worker has no Supabase Storage credentials and no DB access. The
backend already uploaded each file and handed us a signed URL
(contracts.models.FileRef) — we just fetch the bytes over HTTPS.
"""

from __future__ import annotations

import httpx

from contracts.models import FileRef
from seed.normalizer import UploadedFile


class FileFetchError(Exception):
    """A signed URL expired, 404'd, or the fetch otherwise failed. The
    caller treats this exactly like SeedExtractionError — the file is
    reported as rejected, generation continues with whatever else is
    usable."""


async def fetch_uploaded_file(ref: FileRef, *, timeout_s: float = 30.0) -> UploadedFile:
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.get(ref.url)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise FileFetchError(f"Couldn't download {ref.file_name!r}: {exc}") from exc
    return UploadedFile(
        file_name=ref.file_name,
        mime_type=ref.mime_type,
        data=resp.content,
        storage_key=ref.storage_key,
    )


async def fetch_all(
    refs: list[FileRef], *, timeout_s: float = 30.0
) -> tuple[list[UploadedFile], list[tuple[str, str]]]:
    """Returns (successfully fetched files, [(file_name, reason), ...] for
    ones that couldn't be fetched) — fetch failures are folded into the
    same "rejected file" reporting path as extraction failures, rather
    than aborting the whole seed over one bad URL."""
    files: list[UploadedFile] = []
    failed: list[tuple[str, str]] = []
    for ref in refs:
        try:
            files.append(await fetch_uploaded_file(ref, timeout_s=timeout_s))
        except FileFetchError as exc:
            failed.append((ref.file_name, str(exc)))
    return files, failed
