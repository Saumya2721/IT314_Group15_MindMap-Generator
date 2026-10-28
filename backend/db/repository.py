"""
Backend repository: every SQL statement the backend runs against
PostgreSQL / Supabase through asyncpg.

Every function takes an asyncpg Connection *or* Pool as its first argument
(both expose fetch/fetchrow/fetchval/execute/executemany), so routes can pass
`request.app.state.pool` directly or a connection from `transaction(pool)`.

The schema these statements rely on lives in supabase/migrations/ and is
checked by supabase/tests/database/03_backend_contract.test.sql, which
replays the SQL below with untyped parameters exactly as asyncpg sends it.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg

from .models import (
    EDGE_LABEL_MAX,
    ERROR_MESSAGE_MAX,
    FILE_NAME_MAX,
    MIME_TYPE_MAX,
    NODE_LABEL_MAX,
    NODE_TYPE_MAX,
    EdgeRecord,
    EmbeddingRecord,
    EmbeddingSource,
    GenerationJob,
    JobStatus,
    JobType,
    NodeRecord,
    SeedAsset,
    SeedAssetType,
)

# Connection or Pool: both have the same query methods.
Executor = Any

ACTIVE_FULL_MAP_JOB_INDEX = "uq_generation_jobs_active_full_map"

# A generate / regenerate_full job still pending or running after this many
# seconds is treated as abandoned (worker crashed, Redis lost the task, ARQ
# timed out without a callback). Must be well above the worker's
# ARQ_JOB_TIMEOUT_S (300 s by default).
JOB_STALE_AFTER_S = float(os.getenv("GENERATION_JOB_STALE_AFTER_S", "1800"))
STALE_JOB_MESSAGE = "Generation timed out. Please try again."

_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_SHA256_RE = re.compile(r"^[0-9A-Fa-f]{64}$")


class ActiveJobAlreadyExists(Exception):
    """Raised when starting a job while one is already pending/running for the map."""


# ---------------------------------------------------------------------
# Value clean-up: keep worker / LLM output inside the column constraints
# ---------------------------------------------------------------------


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return str(value)[:limit]


def _color(value: str | None) -> str | None:
    """nodes.background_color / text_color must be '#RRGGBB' or NULL."""
    return value if value and _HEX_COLOR_RE.match(value) else None


def _enum_value(value: Any) -> str:
    return str(value.value if hasattr(value, "value") else value)


def _row_to_job(row: Any) -> GenerationJob:
    return GenerationJob(
        id=row["id"],
        map_id=row["map_id"],
        triggered_by_message_id=row["triggered_by_message_id"],
        job_type=JobType(row["job_type"]),
        target_node_id=row["target_node_id"],
        status=JobStatus(row["status"]),
        progress_percent=row["progress_percent"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        callback_token_hash=row.get("callback_token_hash"),
    )


def _job_dict(row: Any) -> dict:
    d = dict(row)
    if d.get("error_message"):
        d["error_message"] = d["error_message"][:500]
    return d


# ---------------------------------------------------------------------
# Mind maps & ownership
# ---------------------------------------------------------------------

MAP_COLS = "id, title, is_public, created_at, updated_at"


async def create_mind_map(conn: Executor, user_id: UUID, title: str = "Untitled mind map") -> dict:
    title = (title or "").strip()[:255] or "Untitled mind map"
    row = await conn.fetchrow(
        f"INSERT INTO public.mind_maps (user_id, title) VALUES ($1, $2) RETURNING {MAP_COLS}",
        user_id,
        title,
    )
    return dict(row)


async def list_mind_maps(conn: Executor, user_id: UUID) -> list[dict]:
    rows = await conn.fetch(
        f"SELECT {MAP_COLS} FROM public.mind_maps WHERE user_id = $1 ORDER BY updated_at DESC",
        user_id,
    )
    return [dict(r) for r in rows]


async def get_mind_map(conn: Executor, map_id: UUID) -> dict | None:
    row = await conn.fetchrow(
        f"SELECT {MAP_COLS}, user_id FROM public.mind_maps WHERE id = $1",
        map_id,
    )
    return dict(row) if row else None


async def assert_map_owner(conn: Executor, map_id: UUID, user_id: UUID) -> bool:
    ok = await conn.fetchval(
        "SELECT 1 FROM public.mind_maps WHERE id = $1 AND user_id = $2", map_id, user_id
    )
    return bool(ok)


async def delete_mind_map(conn: Executor, map_id: UUID) -> list[str]:
    """Delete a map (rows cascade) and return its seed file storage keys.

    Storage objects are not removed by SQL; the caller should delete the
    returned keys from the "seed-assets" bucket with the Storage API.
    """
    keys = await conn.fetch(
        """SELECT a.storage_key
             FROM public.seed_assets a
             JOIN public.generation_jobs j ON j.id = a.generation_job_id
            WHERE j.map_id = $1 AND a.storage_key IS NOT NULL""",
        map_id,
    )
    await conn.execute("DELETE FROM public.mind_maps WHERE id = $1", map_id)
    return [r["storage_key"] for r in keys]


# ---------------------------------------------------------------------
# Chat threads & messages
# ---------------------------------------------------------------------


async def get_or_create_chat_thread(conn: Executor, map_id: UUID, user_id: UUID) -> UUID:
    """Return the map's single chat thread, creating it on first use.

    Safe under concurrency: two first requests for the same map both get the
    same thread instead of one failing on the UNIQUE (map_id) constraint.
    """
    row = await conn.fetchrow("SELECT id FROM public.chat_threads WHERE map_id = $1", map_id)
    if row:
        return row["id"]
    row = await conn.fetchrow(
        "INSERT INTO public.chat_threads (map_id, user_id) VALUES ($1, $2) "
        "ON CONFLICT (map_id) DO NOTHING RETURNING id",
        map_id,
        user_id,
    )
    if row:
        return row["id"]
    row = await conn.fetchrow("SELECT id FROM public.chat_threads WHERE map_id = $1", map_id)
    return row["id"]


async def insert_chat_message(conn: Executor, thread_id: UUID, role: str, content: str) -> UUID:
    row = await conn.fetchrow(
        "INSERT INTO public.chat_messages (thread_id, role, content) "
        "VALUES ($1, $2, $3) RETURNING id",
        thread_id,
        _enum_value(role),
        content,
    )
    return row["id"]


async def get_chat_messages(conn: Executor, map_id: UUID) -> list[dict]:
    rows = await conn.fetch(
        """SELECT m.id, m.role, m.content, m.created_at
             FROM public.chat_messages m JOIN public.chat_threads t ON t.id = m.thread_id
            WHERE t.map_id = $1 ORDER BY m.created_at, m.id""",
        map_id,
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------
# Generation jobs
#
# Lifecycle (CHECK chk_generation_job_lifecycle):
#   pending   : not started, 0 %, no error
#   running   : started_at set, 0-99 %, no error
#   completed : started_at + completed_at set, 100 %, no error
#   failed    : started_at + completed_at set, 0-99 %, error_message set
#   rejected  : never started (started_at NULL), completed_at set, 0 %, error set
# Only one generate/regenerate_full job may be pending/running per map
# (unique index uq_generation_jobs_active_full_map).
# ---------------------------------------------------------------------

JOB_COLS = (
    "id, map_id, job_type, status, progress_percent, error_message, "
    "created_at, started_at, completed_at"
)


async def expire_stale_jobs(
    conn: Executor, map_id: UUID, stale_after_s: float = JOB_STALE_AFTER_S
) -> int:
    """Close abandoned full-map jobs so the map is not blocked forever.

    Never-started jobs become 'rejected', started ones 'failed'.
    Returns how many jobs were closed.
    """
    rows = await conn.fetch(
        """
        UPDATE public.generation_jobs
           SET status = CASE WHEN started_at IS NULL THEN 'rejected' ELSE 'failed' END,
               completed_at = now(),
               progress_percent = CASE WHEN started_at IS NULL THEN 0
                                       ELSE LEAST(progress_percent, 99) END,
               error_message = $3
         WHERE map_id = $1
           AND job_type IN ('generate', 'regenerate_full')
           AND status IN ('pending', 'running')
           AND COALESCE(started_at, created_at) < now() - make_interval(secs => $2)
        RETURNING id
        """,
        map_id,
        float(stale_after_s),
        STALE_JOB_MESSAGE,
    )
    return len(rows)


async def has_active_job(
    conn: Executor, map_id: UUID, stale_after_s: float = JOB_STALE_AFTER_S
) -> bool:
    """True if a generate/regenerate_full job is pending or running for the map.

    Abandoned jobs older than `stale_after_s` are closed in the same statement
    (see expire_stale_jobs), so a crashed worker cannot lock a map with
    "Generation already in progress" forever.
    """
    return bool(
        await conn.fetchval(
            """
            WITH expired AS (
                UPDATE public.generation_jobs
                   SET status = CASE WHEN started_at IS NULL THEN 'rejected' ELSE 'failed' END,
                       completed_at = now(),
                       progress_percent = CASE WHEN started_at IS NULL THEN 0
                                               ELSE LEAST(progress_percent, 99) END,
                       error_message = $3
                 WHERE map_id = $1
                   AND job_type IN ('generate', 'regenerate_full')
                   AND status IN ('pending', 'running')
                   AND COALESCE(started_at, created_at) < now() - make_interval(secs => $2)
                RETURNING id
            )
            SELECT 1 FROM public.generation_jobs
             WHERE map_id = $1
               AND job_type IN ('generate', 'regenerate_full')
               AND status IN ('pending', 'running')
               AND id NOT IN (SELECT id FROM expired)
             LIMIT 1
            """,
            map_id,
            float(stale_after_s),
            STALE_JOB_MESSAGE,
        )
    )


async def create_generation_job(
    conn: Executor,
    *,
    map_id: UUID,
    job_type: JobType | str,
    job_id: UUID | None = None,
    triggered_by_message_id: UUID | None = None,
    target_node_id: UUID | None = None,
    callback_token_hash: str | None = None,
) -> GenerationJob:
    """Insert a 'pending' job. Raises ActiveJobAlreadyExists when the map
    already has an active generate/regenerate_full job."""
    job_type_str = _enum_value(job_type)
    try:
        if job_id is not None:
            row = await conn.fetchrow(
                """
                INSERT INTO public.generation_jobs
                    (id, map_id, triggered_by_message_id, job_type, target_node_id,
                     callback_token_hash)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING *
                """,
                job_id,
                map_id,
                triggered_by_message_id,
                job_type_str,
                target_node_id,
                callback_token_hash,
            )
        else:
            row = await conn.fetchrow(
                """
                INSERT INTO public.generation_jobs
                    (map_id, triggered_by_message_id, job_type, target_node_id,
                     callback_token_hash)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING *
                """,
                map_id,
                triggered_by_message_id,
                job_type_str,
                target_node_id,
                callback_token_hash,
            )
    except asyncpg.UniqueViolationError as exc:
        constraint = getattr(exc, "constraint_name", None)
        if constraint == ACTIVE_FULL_MAP_JOB_INDEX or ACTIVE_FULL_MAP_JOB_INDEX in str(exc):
            raise ActiveJobAlreadyExists(
                f"A generate/regenerate_full job is already pending or running for map {map_id}"
            ) from exc
        raise
    return _row_to_job(row)


async def update_job_status(
    conn: Executor,
    *,
    job_id: UUID,
    status: JobStatus | str,
    progress_percent: int | None = None,
    error_message: str | None = None,
) -> None:
    """Move a job to `status`, filling in whatever the lifecycle CHECK needs
    (timestamps, progress, error message) so the update can never violate it."""
    status = JobStatus(_enum_value(status))
    parts = ["status = $2"]
    args: list[Any] = [job_id, status.value]

    def add(expr: str, value: Any) -> None:
        args.append(value)
        parts.append(expr.format(f"${len(args)}"))

    if status == JobStatus.PENDING:
        parts += [
            "started_at = NULL",
            "completed_at = NULL",
            "progress_percent = 0",
            "error_message = NULL",
        ]
    elif status == JobStatus.RUNNING:
        parts += [
            "started_at = COALESCE(started_at, now())",
            "completed_at = NULL",
            "error_message = NULL",
        ]
        if progress_percent is not None:
            add("progress_percent = {}", max(0, min(99, int(progress_percent))))
        else:
            parts.append("progress_percent = LEAST(progress_percent, 99)")
    elif status == JobStatus.COMPLETED:
        parts += [
            "started_at = COALESCE(started_at, now())",
            "completed_at = COALESCE(completed_at, now())",
            "progress_percent = 100",
            "error_message = NULL",
        ]
    elif status == JobStatus.FAILED:
        parts += [
            "started_at = COALESCE(started_at, now())",
            "completed_at = COALESCE(completed_at, now())",
        ]
        if progress_percent is not None:
            add("progress_percent = {}", max(0, min(99, int(progress_percent))))
        else:
            parts.append("progress_percent = LEAST(progress_percent, 99)")
        add("error_message = {}", (error_message or "Generation failed")[:ERROR_MESSAGE_MAX])
    else:  # REJECTED: only valid for a job that never started
        parts += [
            "started_at = NULL",
            "completed_at = COALESCE(completed_at, now())",
            "progress_percent = 0",
        ]
        add("error_message = {}", (error_message or "Generation rejected")[:ERROR_MESSAGE_MAX])

    sql = f"UPDATE public.generation_jobs SET {', '.join(parts)} WHERE id = $1"
    await conn.execute(sql, *args)


async def mark_job_running(conn: Executor, job_id: UUID) -> None:
    await conn.execute(
        """
        UPDATE public.generation_jobs
           SET status = 'running', started_at = now(), progress_percent = 0
         WHERE id = $1 AND status = 'pending'
        """,
        job_id,
    )


async def update_job_progress(conn: Executor, job_id: UUID, percent: int) -> None:
    """Monotonic progress for a running job, capped at 99 (100 = completed)."""
    percent = max(0, min(99, int(percent)))
    await conn.execute(
        """
        UPDATE public.generation_jobs
           SET progress_percent = GREATEST(progress_percent, $2)
         WHERE id = $1 AND status = 'running'
        """,
        job_id,
        percent,
    )


async def mark_job_completed(conn: Executor, job_id: UUID) -> None:
    await conn.execute(
        """
        UPDATE public.generation_jobs
           SET status = 'completed', progress_percent = 100, completed_at = now()
         WHERE id = $1 AND status = 'running'
        """,
        job_id,
    )


async def mark_job_failed(conn: Executor, job_id: UUID, error_message: str) -> None:
    await conn.execute(
        """
        UPDATE public.generation_jobs
           SET status = 'failed', completed_at = now(), error_message = $2
         WHERE id = $1 AND status = 'running'
        """,
        job_id,
        (error_message or "Generation failed")[:ERROR_MESSAGE_MAX],
    )


async def mark_job_rejected(conn: Executor, job_id: UUID, error_message: str) -> None:
    await conn.execute(
        """
        UPDATE public.generation_jobs
           SET status = 'rejected', completed_at = now(), error_message = $2
         WHERE id = $1 AND status = 'pending'
        """,
        job_id,
        (error_message or "Generation rejected")[:ERROR_MESSAGE_MAX],
    )


async def get_latest_job(conn: Executor, map_id: UUID) -> dict | None:
    row = await conn.fetchrow(
        f"SELECT {JOB_COLS} FROM public.generation_jobs "
        "WHERE map_id = $1 ORDER BY created_at DESC LIMIT 1",
        map_id,
    )
    return _job_dict(row) if row else None


async def get_job(conn: Executor, map_id: UUID, job_id: UUID) -> dict | None:
    row = await conn.fetchrow(
        f"SELECT {JOB_COLS} FROM public.generation_jobs WHERE id = $1 AND map_id = $2",
        job_id,
        map_id,
    )
    return _job_dict(row) if row else None


# ---------------------------------------------------------------------
# Graph persistence (nodes & edges)
# ---------------------------------------------------------------------


def _node_row(n: NodeRecord, map_id: UUID, source_job_id: UUID | None) -> tuple:
    label = (_clip(n.label, NODE_LABEL_MAX) or "").strip() or "Untitled"
    node_type = (_clip(n.node_type, NODE_TYPE_MAX) or "").strip() or "default"
    return (
        n.id,
        map_id,
        label,
        n.description,
        float(n.pos_x or 0.0),
        float(n.pos_y or 0.0),
        _color(n.background_color),
        _color(n.text_color),
        node_type,
        source_job_id,
        n.node_data,
    )


def _edge_rows(edges: list[EdgeRecord], map_id: UUID) -> list[tuple]:
    """Drop self-loops and exact duplicates (same source, target and label).

    The LLM validator does not reject duplicate edges, but the database does
    (uq_edges_source_target_label); without this one repeated edge in a
    worker result failed the whole completion callback and left the job
    'running'.
    """
    seen: set[tuple[UUID, UUID, str | None]] = set()
    rows = []
    for e in edges:
        if e.source_node_id == e.target_node_id:
            continue
        label = _clip(e.label, EDGE_LABEL_MAX)
        key = (e.source_node_id, e.target_node_id, label)
        if key in seen:
            continue
        seen.add(key)
        rows.append((e.id, map_id, e.source_node_id, e.target_node_id, label, e.edge_data))
    return rows


async def persist_generated_graph(
    conn: Executor,
    *,
    map_id: UUID,
    source_job_id: UUID | None,
    nodes: list[NodeRecord],
    edges: list[EdgeRecord],
) -> None:
    """Insert nodes, then edges, into public.nodes / public.edges.

    Run inside a transaction (the callers do) so a failure leaves no half graph.
    """
    if nodes:
        await conn.executemany(
            """
            INSERT INTO public.nodes
                (id, map_id, label, description, pos_x, pos_y,
                 background_color, text_color, node_type, source_job_id, node_data)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            [_node_row(n, map_id, source_job_id) for n in nodes],
        )
    edge_rows = _edge_rows(edges, map_id)
    if edge_rows:
        await conn.executemany(
            """
            INSERT INTO public.edges (id, map_id, source_node_id, target_node_id, label, edge_data)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT DO NOTHING
            """,
            edge_rows,
        )


async def replace_map_graph(
    conn: Executor,
    *,
    map_id: UUID,
    source_job_id: UUID | None,
    nodes: list[NodeRecord],
    edges: list[EdgeRecord],
) -> None:
    """Replace the whole graph (full regeneration). Run inside a transaction."""
    await conn.execute("DELETE FROM public.edges WHERE map_id = $1", map_id)
    await conn.execute("DELETE FROM public.nodes WHERE map_id = $1", map_id)
    await persist_generated_graph(
        conn, map_id=map_id, source_job_id=source_job_id, nodes=nodes, edges=edges
    )


async def get_map_graph(conn: Executor, map_id: UUID) -> dict:
    """Full graph of a map, in a stable order."""
    nodes = await conn.fetch(
        """SELECT id, label, description, pos_x, pos_y, background_color, text_color,
                  width, height, node_type, node_data
             FROM public.nodes WHERE map_id = $1 ORDER BY created_at, id""",
        map_id,
    )
    edges = await conn.fetch(
        """SELECT id, source_node_id, target_node_id, label, edge_data
             FROM public.edges WHERE map_id = $1 ORDER BY created_at, id""",
        map_id,
    )
    return {
        "nodes": [dict(n) for n in nodes],
        "edges": [dict(e) for e in edges],
    }


# ---------------------------------------------------------------------
# Seed assets
# ---------------------------------------------------------------------


async def insert_seed_asset(
    conn: Executor,
    *,
    generation_job_id: UUID,
    asset_type: SeedAssetType | str,
    file_name: str | None,
    storage_key: str | None,
    mime_type: str | None,
    content_text: str | None,
    file_size_bytes: int | None,
    checksum_sha256: str | None,
) -> SeedAsset:
    asset_type = SeedAssetType(_enum_value(asset_type).lower())
    if asset_type == SeedAssetType.TEXT:
        storage_key = None  # text seeds are never stored as files
    elif not file_name and storage_key:
        file_name = storage_key.rsplit("/", 1)[-1]
    if checksum_sha256 and not _SHA256_RE.match(checksum_sha256):
        checksum_sha256 = None
    if file_size_bytes is not None and file_size_bytes < 0:
        file_size_bytes = None

    row = await conn.fetchrow(
        """
        INSERT INTO public.seed_assets
            (generation_job_id, asset_type, file_name, storage_key, mime_type,
             content_text, file_size_bytes, checksum_sha256)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING *
        """,
        generation_job_id,
        asset_type.value,
        _clip(file_name, FILE_NAME_MAX),
        storage_key,
        _clip(mime_type, MIME_TYPE_MAX),
        content_text,
        file_size_bytes,
        checksum_sha256.lower() if checksum_sha256 else None,
    )
    return SeedAsset(
        id=row["id"],
        generation_job_id=row["generation_job_id"],
        asset_type=SeedAssetType(row["asset_type"]),
        file_name=row["file_name"],
        storage_key=row["storage_key"],
        mime_type=row["mime_type"],
        content_text=row["content_text"],
        file_size_bytes=row["file_size_bytes"],
        checksum_sha256=row["checksum_sha256"],
    )


async def mark_seed_asset_extracted(
    conn: Executor,
    asset_id: UUID,
    content_text: str,
    file_size_bytes: int | None = None,
    checksum_sha256: str | None = None,
) -> None:
    await conn.execute(
        """
        UPDATE public.seed_assets
           SET content_text = $2,
               file_size_bytes = COALESCE($3, file_size_bytes),
               checksum_sha256 = COALESCE($4, checksum_sha256)
         WHERE id = $1
        """,
        asset_id,
        content_text,
        file_size_bytes,
        checksum_sha256.lower() if checksum_sha256 else None,
    )


async def get_original_seed_text(conn: Executor, map_id: UUID) -> str | None:
    """Text of the map's first seed (used by full regeneration)."""
    return await conn.fetchval(
        """SELECT a.content_text
             FROM public.seed_assets a
             JOIN public.generation_jobs j ON j.id = a.generation_job_id
            WHERE j.map_id = $1 AND a.content_text IS NOT NULL
            ORDER BY a.uploaded_at ASC LIMIT 1""",
        map_id,
    )


async def has_seed(conn: Executor, map_id: UUID) -> bool:
    return bool(
        await conn.fetchval(
            """SELECT 1 FROM public.seed_assets a
                 JOIN public.generation_jobs j ON j.id = a.generation_job_id
                WHERE j.map_id = $1 AND a.content_text IS NOT NULL LIMIT 1""",
            map_id,
        )
    )


# ---------------------------------------------------------------------
# Embeddings (provenance only; vectors live in Pinecone, namespace = map_id)
# ---------------------------------------------------------------------


def _retrieved_at(source: EmbeddingSource) -> datetime | None:
    return datetime.now(UTC) if source != EmbeddingSource.UPLOADED_SEED else None


async def insert_embeddings(conn: Executor, records: list[EmbeddingRecord]) -> None:
    """Store chunk text + Pinecone vector IDs for a batch of chunks."""
    if not records:
        return
    await conn.executemany(
        """
        INSERT INTO public.embeddings
            (generation_job_id, map_id, chunk_text, pinecone_vector_id, embedding_model,
             source, source_title, source_url, retrieved_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
        [
            (
                r.generation_job_id,
                r.map_id,
                r.chunk_text,
                r.pinecone_vector_id,
                r.embedding_model,
                EmbeddingSource(_enum_value(r.source)).value,
                r.source_title,
                r.source_url,
                _retrieved_at(EmbeddingSource(_enum_value(r.source))),
            )
            for r in records
        ],
    )


async def insert_embedding(
    conn: Executor,
    *,
    generation_job_id: UUID,
    map_id: UUID,
    chunk_text: str,
    embedding_model: str,
    source: EmbeddingSource | str,
    pinecone_vector_id: str | None = None,
    source_title: str | None = None,
    source_url: str | None = None,
) -> UUID:
    """Insert a single chunk reference with its Pinecone vector ID."""
    src = EmbeddingSource(_enum_value(source))
    row = await conn.fetchrow(
        """
        INSERT INTO public.embeddings
            (generation_job_id, map_id, chunk_text, pinecone_vector_id, embedding_model,
             source, source_title, source_url, retrieved_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING id
        """,
        generation_job_id,
        map_id,
        chunk_text,
        pinecone_vector_id,
        embedding_model,
        src.value,
        source_title,
        source_url,
        _retrieved_at(src),
    )
    return row["id"]


async def get_map_embeddings(conn: Executor, map_id: UUID) -> list[EmbeddingRecord]:
    """Chunk references and Pinecone vector IDs for a mind map."""
    rows = await conn.fetch(
        """
        SELECT id, generation_job_id, map_id, chunk_text, pinecone_vector_id,
               embedding_model, source, source_title, source_url
          FROM public.embeddings
         WHERE map_id = $1
         ORDER BY created_at, id
        """,
        map_id,
    )
    return [
        EmbeddingRecord(
            id=r["id"],
            generation_job_id=r["generation_job_id"],
            map_id=r["map_id"],
            chunk_text=r["chunk_text"],
            pinecone_vector_id=r["pinecone_vector_id"],
            embedding_model=r["embedding_model"],
            source=EmbeddingSource(r["source"]),
            source_title=r["source_title"],
            source_url=r["source_url"],
        )
        for r in rows
    ]


async def hybrid_retrieve(
    conn: Executor,
    *,
    map_id: UUID,
    query_embedding: list[float],
    query_text: str,
    top_k: int,
) -> list[EmbeddingRecord]:
    """[DEPRECATED] Retrieval runs in the LLM worker on Pinecone
    (llm/mindmap_llm/rag/pinecone_store.py), not in Postgres."""
    raise NotImplementedError(
        "PostgreSQL pgvector retrieval was removed. Vector search runs in the "
        "LLM worker via PineconeVectorStore."
    )
