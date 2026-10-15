"""
CHANGED in the worker restructure: normalize_seed() used to call
insert_seed_asset() against Postgres for every accepted file/text block.
It now builds SeedAssetDraft objects instead and returns them as part of
NormalizedSeed — the backend inserts the actual seed_assets rows from
GenerationResult.seed_assets once the callback arrives. Everything about
*how* files are validated and extracted (DR-02: distinct parsing per
type, 25MB cap, Jev pre-filter) is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from contracts.models import SeedAssetDraft, SeedAssetType
from providers.decision_model import DecisionModel
from seed.extractors import (
    SeedExtractionError,
    checksum_sha256,
    extract_text_from_image,
    extract_text_from_pdf,
    normalize_free_text,
)

MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB; enforce before expensive parsing (US-02 tech note)
SUPPORTED_MIME_PREFIXES = ("application/pdf", "image/")


@dataclass
class UploadedFile:
    """File bytes already fetched (worker/files.py) from the backend's
    signed URL — this module never deals with URLs, only bytes."""
    file_name: str
    mime_type: str
    data: bytes
    storage_key: str | None = None  # carried through to SeedAssetDraft so the backend can match it to its own upload record


@dataclass
class SeedSubmission:
    topic: str | None
    ideas: str | None
    files: list[UploadedFile] = field(default_factory=list)


@dataclass
class NormalizedSeed:
    """The single combined Seed object generation consumes (FR-06)."""
    combined_text: str
    seed_assets: list[SeedAssetDraft]
    rejected_files: list[tuple[str, str]]  # (file_name, reason) — Scenario 4: report clearly, don't block


class NoUsableSeedContent(Exception):
    """Raised only if EVERY input failed and there is genuinely nothing to
    generate from — the one case where seed submission itself must fail."""


async def _classify_is_supported_upload(
    decision_model: DecisionModel | None, file_name: str, mime_type: str, size_bytes: int
) -> bool:
    """Cheap Jev-backed gate: catch obviously-wrong uploads before running
    real parsing. Purely an optimization — real validation below still
    always runs regardless of this call's answer."""
    if decision_model is None:
        return True
    try:
        result = await decision_model.classify(
            state=f"file_name={file_name!r} mime_type={mime_type!r} size_bytes={size_bytes}",
            question="Is this file plausibly a valid, parseable PDF or image upload?",
            labels=["plausible", "implausible"],
        )
        return result.label == "plausible"
    except Exception:
        return True  # never let this optimization block a real upload attempt


async def normalize_seed(
    *,
    submission: SeedSubmission,
    decision_model: DecisionModel | None = None,
) -> NormalizedSeed:
    text_parts: list[str] = []
    seed_assets: list[SeedAssetDraft] = []
    rejected: list[tuple[str, str]] = []

    base_text = normalize_free_text(submission.topic, submission.ideas) if (submission.topic or submission.ideas) else None
    if base_text:
        text_parts.append(base_text)

    for f in submission.files:
        try:
            if len(f.data) == 0:
                raise SeedExtractionError("The file is empty.")
            if len(f.data) > MAX_FILE_SIZE_BYTES:
                raise SeedExtractionError(f"File exceeds the {MAX_FILE_SIZE_BYTES // (1024*1024)} MB limit.")
            if not f.mime_type.startswith(SUPPORTED_MIME_PREFIXES):
                raise SeedExtractionError(f"Unsupported file type: {f.mime_type}")

            if not await _classify_is_supported_upload(decision_model, f.file_name, f.mime_type, len(f.data)):
                raise SeedExtractionError("This file doesn't look like a valid PDF or image.")

            if f.mime_type.startswith("application/pdf"):
                extracted = extract_text_from_pdf(f.data)
                asset_type = SeedAssetType.PDF
            else:
                extracted = extract_text_from_image(f.data)
                asset_type = SeedAssetType.IMAGE

            seed_assets.append(SeedAssetDraft(
                asset_type=asset_type,
                file_name=f.file_name,
                storage_key=f.storage_key,
                mime_type=f.mime_type,
                content_text=extracted,
                file_size_bytes=len(f.data),
                checksum_sha256=checksum_sha256(f.data),
            ))
            text_parts.append(f"[From {f.file_name}]\n{extracted}")

        except SeedExtractionError as exc:
            # Scenario 4: report clearly, but DO NOT block the rest of the seed.
            rejected.append((f.file_name, str(exc)))

    if base_text:
        seed_assets.append(SeedAssetDraft(
            asset_type=SeedAssetType.TEXT, file_name=None, storage_key=None,
            mime_type=None, content_text=base_text,
            file_size_bytes=len(base_text.encode("utf-8")), checksum_sha256=None,
        ))

    if not text_parts:
        raise NoUsableSeedContent(
            "No usable seed content: "
            + ("; ".join(f"{name}: {reason}" for name, reason in rejected) if rejected else "nothing was submitted.")
        )

    return NormalizedSeed(
        combined_text="\n\n---\n\n".join(text_parts),
        seed_assets=seed_assets,
        rejected_files=rejected,
    )
