"""
Backend repository handling all database interactions with PostgreSQL / Supabase.
All SQL operations are centralized here.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from .models import (
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


class ActiveJobAlreadyExists(Exception):
    """Raised when starting a job while one is already pending/running for the map."""


def _row_to_job(row: asyncpg.Record) -> GenerationJob:
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
        callback_token_hash=row.get("callback_token_hash", None),
    )


# ---------------------------------------------------------------------
# Mind Maps & Ownership
# ---------------------------------------------------------------------


async def create_mind_map(conn: asyncpg.Connection, user_id: UUID, title: str = "Untitled mind map") -> dict:
    clean_title = (title or "").strip()[:255] or "Untitled mind map"
    row = await conn.fetchrow(
        "INSERT INTO public.mind_maps (user_id, title) VALUES ($1, $2) "
        "RETURNING id, title, is_public, created_at, updated_at",
        user_id,
        clean_title,
    )
    return dict(row)


async def list_mind_maps(conn: asyncpg.Connection, user_id: UUID) -> list[dict]:
    rows = await conn.fetch(
        "SELECT id, title, is_public, created_at, updated_at FROM public.mind_maps "
        "WHERE user_id = $1 ORDER BY updated_at DESC",
        user_id,
    )
    return [dict(r) for r in rows]


async def get_mind_map(conn: asyncpg.Connection, map_id: UUID) -> dict | None:
    row = await conn.fetchrow(
        "SELECT id, user_id, title, is_public, created_at, updated_at FROM public.mind_maps WHERE id = $1",
        map_id,
    )
    return dict(row) if row else None


async def assert_map_owner(conn: asyncpg.Connection, map_id: UUID, user_id: UUID) -> bool:
    ok = await conn.fetchval("SELECT 1 FROM public.mind_maps WHERE id = $1 AND user_id = $2", map_id, user_id)
    return bool(ok)


async def search_maps(
    conn: asyncpg.Connection,
    user_id: UUID,
    query: str,
    limit: int = 20,
) -> list[dict]:
    """Search maps belonging to ``user_id`` by title / description (S2-FE5 / US-08).

    Results are ordered by relevance — maps whose title matches rank above
    maps that only match in the description — and then by ``updated_at``.
    The literal search term is supplied as a parameter; ``%``/``_`` in the
    term are escaped so user input never alters the pattern's meaning.
    """
    term = (query or "").strip().lower()
    if not term:
        return []
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"

    rows = await conn.fetch(
        """SELECT m.id, m.title, m.description, m.is_public, m.created_at, m.updated_at,
                  (SELECT count(*)::int FROM public.nodes n WHERE n.map_id = m.id) AS node_count,
                  (SELECT j.status FROM public.generation_jobs j WHERE j.map_id = m.id ORDER BY j.created_at DESC LIMIT 1) AS latest_job_status,
                  (SELECT j.progress_percent::int FROM public.generation_jobs j WHERE j.map_id = m.id ORDER BY j.created_at DESC LIMIT 1) AS latest_job_progress,
                  (SELECT j.id FROM public.generation_jobs j WHERE j.map_id = m.id ORDER BY j.created_at DESC LIMIT 1) AS latest_job_id
           FROM public.mind_maps m
           WHERE m.user_id = $1
             AND (m.title ILIKE $2 OR COALESCE(m.description, '') ILIKE $2 OR EXISTS (SELECT 1 FROM public.nodes n WHERE n.map_id = m.id AND n.label ILIKE $2))
           ORDER BY (m.title ILIKE $2) DESC, m.updated_at DESC
           LIMIT $3""",
        user_id,
        pattern,
        limit,
    )
    return [dict(r) for r in rows]


async def update_mind_map_occ(
    conn: asyncpg.Connection,
    *,
    map_id: UUID,
    user_id: UUID,
    expected_updated_at: datetime | None = None,
    title: str | None = None,
    description: str | None = None,
    is_public: bool | None = None,
    view_x: float | None = None,
    view_y: float | None = None,
    view_zoom: float | None = None,
) -> tuple[dict | None, str | None]:
    """Update mind map with optimistic concurrency control.

    Returns (updated_row, conflict_status).
    If expected_updated_at is provided and doesn't match, returns (None, 'CONFLICT').
    """
    if expected_updated_at is not None:
        row = await conn.fetchrow(
            """UPDATE public.mind_maps
               SET title = COALESCE($3, title),
                   description = COALESCE($4, description),
                   is_public = COALESCE($5, is_public),
                   view_x = COALESCE($6, view_x),
                   view_y = COALESCE($7, view_y),
                   view_zoom = COALESCE($8, view_zoom),
                   updated_at = now()
               WHERE id = $1 AND user_id = $2 AND updated_at = $9
               RETURNING id, title, description, is_public, created_at, updated_at""",
            map_id,
            user_id,
            title,
            description,
            is_public,
            view_x,
            view_y,
            view_zoom,
            expected_updated_at,
        )
        if row:
            return dict(row), None
        current_dt = await conn.fetchval(
            "SELECT updated_at FROM public.mind_maps WHERE id = $1 AND user_id = $2",
            map_id,
            user_id,
        )
        if current_dt is not None:
            return None, "CONFLICT"
        return None, "NOT_FOUND"
    else:
        row = await conn.fetchrow(
            """UPDATE public.mind_maps
               SET title = COALESCE($3, title),
                   description = COALESCE($4, description),
                   is_public = COALESCE($5, is_public),
                   view_x = COALESCE($6, view_x),
                   view_y = COALESCE($7, view_y),
                   view_zoom = COALESCE($8, view_zoom),
                   updated_at = now()
               WHERE id = $1 AND user_id = $2
               RETURNING id, title, description, is_public, created_at, updated_at""",
            map_id,
            user_id,
            title,
            description,
            is_public,
            view_x,
            view_y,
            view_zoom,
        )
        if row:
            return dict(row), None
        return None, "NOT_FOUND"


# ---------------------------------------------------------------------
# Chat Threads & Messages
# ---------------------------------------------------------------------


async def get_or_create_chat_thread(conn: asyncpg.Connection, map_id: UUID, user_id: UUID) -> UUID:
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
    # Another request created the map's single chat thread first.
    existing = await conn.fetchrow("SELECT id FROM public.chat_threads WHERE map_id = $1", map_id)
    if existing is None:
        raise RuntimeError("Chat thread disappeared after concurrent creation")
    return existing["id"]


async def insert_chat_message(conn: asyncpg.Connection, thread_id: UUID, role: str, content: str) -> UUID:
    row = await conn.fetchrow(
        "INSERT INTO public.chat_messages (thread_id, role, content) VALUES ($1, $2, $3) RETURNING id",
        thread_id,
        role,
        content,
    )
    return row["id"]


async def get_chat_messages(conn: asyncpg.Connection, map_id: UUID) -> list[dict]:
    rows = await conn.fetch(
        """SELECT m.id, m.role, m.content, m.created_at
             FROM public.chat_messages m JOIN public.chat_threads t ON t.id = m.thread_id
            WHERE t.map_id = $1 ORDER BY m.created_at, m.id""",
        map_id,
    )
    return [dict(r) for r in rows]


async def get_chat_thread_summary(conn: asyncpg.Connection, map_id: UUID) -> str | None:
    row = await conn.fetchrow("SELECT summary FROM public.chat_threads WHERE map_id = $1", map_id)
    return row["summary"] if row and row["summary"] else None


async def update_chat_thread_summary(conn: asyncpg.Connection, map_id: UUID, summary: str) -> None:
    await conn.execute("UPDATE public.chat_threads SET summary = $2 WHERE map_id = $1", map_id, summary)


async def get_recent_chat_messages(conn: asyncpg.Connection, map_id: UUID, limit: int = 6) -> list[dict]:
    rows = await conn.fetch(
        """SELECT m.id, m.role, m.content, m.created_at
             FROM public.chat_messages m JOIN public.chat_threads t ON t.id = m.thread_id
            WHERE t.map_id = $1
            ORDER BY m.created_at DESC, m.id DESC
            LIMIT $2""",
        map_id,
        limit,
    )
    return [dict(r) for r in reversed(rows)]


# ---------------------------------------------------------------------
# Generation Jobs
# ---------------------------------------------------------------------


async def has_active_job(conn: asyncpg.Connection, map_id: UUID) -> bool:
    return bool(
        await conn.fetchval(
            """SELECT 1 FROM public.generation_jobs
            WHERE map_id = $1 AND job_type IN ('generate', 'regenerate_full')
              AND status IN ('pending', 'running') LIMIT 1""",
            map_id,
        )
    )


async def create_generation_job(
    conn: asyncpg.Connection,
    *,
    map_id: UUID,
    job_type: JobType | str,
    job_id: UUID | None = None,
    triggered_by_message_id: UUID | None = None,
    target_node_id: UUID | None = None,
    callback_token_hash: str | None = None,
) -> GenerationJob:
    job_type_str = job_type.value if hasattr(job_type, "value") else str(job_type)
    try:
        if job_id is not None:
            row = await conn.fetchrow(
                """
                INSERT INTO public.generation_jobs (id, map_id, triggered_by_message_id, job_type, target_node_id, callback_token_hash)
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
                INSERT INTO public.generation_jobs (map_id, triggered_by_message_id, job_type, target_node_id, callback_token_hash)
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
        if "uq_generation_jobs_active_full_map" in str(exc):
            raise ActiveJobAlreadyExists(
                f"A generate/regenerate_full job is already pending or running for map {map_id}"
            ) from exc
        raise
    return _row_to_job(row)


async def update_job_status(
    conn: asyncpg.Connection,
    *,
    job_id: UUID,
    status: JobStatus,
    progress_percent: int | None = None,
    error_message: str | None = None,
) -> None:
    parts = ["status = $2"]
    args: list[Any] = [job_id, status.value]

    if status == JobStatus.RUNNING:
        parts.append("started_at = COALESCE(started_at, now())")
    elif status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.REJECTED):
        parts.append("completed_at = COALESCE(completed_at, now())")

    if progress_percent is not None:
        args.append(progress_percent)
        parts.append(f"progress_percent = ${len(args)}")

    if error_message is not None:
        args.append(error_message)
        parts.append(f"error_message = ${len(args)}")

    sql = f"UPDATE public.generation_jobs SET {', '.join(parts)} WHERE id = $1"
    await conn.execute(sql, *args)


async def mark_job_running(conn: asyncpg.Connection, job_id: UUID) -> None:
    await conn.execute(
        """
        UPDATE public.generation_jobs
        SET status = 'running', started_at = NOW(), progress_percent = 0
        WHERE id = $1 AND status = 'pending'
        """,
        job_id,
    )


async def update_job_progress(conn: asyncpg.Connection, job_id: UUID, percent: int) -> None:
    percent = max(0, min(99, percent))
    await conn.execute(
        "UPDATE public.generation_jobs SET progress_percent = $2 WHERE id = $1 AND status = 'running'",
        job_id,
        percent,
    )


async def mark_job_completed(conn: asyncpg.Connection, job_id: UUID) -> None:
    await conn.execute(
        """
        UPDATE public.generation_jobs
        SET status = 'completed', progress_percent = 100, completed_at = NOW()
        WHERE id = $1 AND status = 'running'
        """,
        job_id,
    )


async def mark_job_failed(conn: asyncpg.Connection, job_id: UUID, error_message: str) -> None:
    await conn.execute(
        """
        UPDATE public.generation_jobs
        SET status = 'failed', completed_at = NOW(), error_message = $2
        WHERE id = $1 AND status = 'running'
        """,
        job_id,
        error_message[:4000],
    )


async def mark_job_rejected(conn: asyncpg.Connection, job_id: UUID, error_message: str) -> None:
    await conn.execute(
        """
        UPDATE public.generation_jobs
        SET status = 'rejected', completed_at = NOW(), error_message = $2
        WHERE id = $1 AND status = 'pending'
        """,
        job_id,
        error_message[:4000],
    )


async def get_latest_job(conn: asyncpg.Connection, map_id: UUID) -> dict | None:
    row = await conn.fetchrow(
        """SELECT id, map_id, job_type, status, progress_percent, error_message, created_at, started_at, completed_at
             FROM public.generation_jobs WHERE map_id = $1 ORDER BY created_at DESC LIMIT 1""",
        map_id,
    )
    if not row:
        return None
    d = dict(row)
    if d.get("error_message"):
        d["error_message"] = d["error_message"][:500]
    return d


async def get_job(conn: asyncpg.Connection, map_id: UUID, job_id: UUID) -> dict | None:
    row = await conn.fetchrow(
        """SELECT id, map_id, job_type, status, progress_percent, error_message, created_at, started_at, completed_at
             FROM public.generation_jobs WHERE id = $1 AND map_id = $2""",
        job_id,
        map_id,
    )
    if not row:
        return None
    d = dict(row)
    if d.get("error_message"):
        d["error_message"] = d["error_message"][:500]
    return d


# ---------------------------------------------------------------------
# Graph Persistence (Nodes & Edges)
# ---------------------------------------------------------------------


async def persist_generated_graph(
    conn: asyncpg.Connection,
    *,
    map_id: UUID,
    source_job_id: UUID | None,
    nodes: list[NodeRecord],
    edges: list[EdgeRecord],
) -> None:
    """Save nodes and edges into public.nodes and public.edges."""
    if nodes:
        await conn.executemany(
            """
            INSERT INTO public.nodes
                (id, map_id, label, description, pos_x, pos_y,
                 background_color, text_color, node_type, source_job_id, node_data)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            [
                (
                    n.id,
                    map_id,
                    n.label,
                    n.description,
                    n.pos_x,
                    n.pos_y,
                    n.background_color,
                    n.text_color,
                    n.node_type,
                    source_job_id,
                    n.node_data,
                )
                for n in nodes
            ],
        )
    if edges:
        await conn.executemany(
            """
            INSERT INTO public.edges (id, map_id, source_node_id, target_node_id, label, edge_data)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            [(e.id, map_id, e.source_node_id, e.target_node_id, e.label, e.edge_data) for e in edges],
        )


async def replace_map_graph(
    conn: asyncpg.Connection,
    *,
    map_id: UUID,
    source_job_id: UUID | None,
    nodes: list[NodeRecord],
    edges: list[EdgeRecord],
) -> None:
    """Replace an existing graph on full regeneration."""
    await conn.execute("DELETE FROM public.edges WHERE map_id = $1", map_id)
    await conn.execute("DELETE FROM public.nodes WHERE map_id = $1", map_id)
    await persist_generated_graph(conn, map_id=map_id, source_job_id=source_job_id, nodes=nodes, edges=edges)


async def get_map_graph(conn: asyncpg.Connection, map_id: UUID) -> dict:
    """Retrieve full nodes and edges graph for a map."""
    nodes = await conn.fetch(
        """SELECT id, label, description, pos_x, pos_y, background_color, text_color,
                  width, height, node_type, node_data
             FROM public.nodes WHERE map_id = $1""",
        map_id,
    )
    edges = await conn.fetch(
        "SELECT id, source_node_id, target_node_id, label, edge_data FROM public.edges WHERE map_id = $1",
        map_id,
    )
    return {
        "nodes": [dict(n) for n in nodes],
        "edges": [dict(e) for e in edges],
    }


# ---------------------------------------------------------------------
# Seed Assets & Embeddings (Optional RAG persistence)
# ---------------------------------------------------------------------


async def insert_seed_asset(
    conn: asyncpg.Connection,
    *,
    generation_job_id: UUID,
    asset_type: SeedAssetType,
    file_name: str | None,
    storage_key: str | None,
    mime_type: str | None,
    content_text: str | None,
    file_size_bytes: int | None,
    checksum_sha256: str | None,
) -> SeedAsset:
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
        file_name,
        storage_key,
        mime_type,
        content_text,
        file_size_bytes,
        checksum_sha256,
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


async def insert_embeddings(conn: asyncpg.Connection, records: list[EmbeddingRecord]) -> None:
    """Store text chunk metadata and Pinecone vector IDs in PostgreSQL for provenance."""
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
                r.source.value,
                r.source_title,
                r.source_url,
                datetime.utcnow() if r.source != EmbeddingSource.UPLOADED_SEED else None,
            )
            for r in records
        ],
    )


async def insert_embedding(
    conn: asyncpg.Connection,
    *,
    generation_job_id: UUID,
    map_id: UUID,
    chunk_text: str,
    embedding_model: str,
    source: EmbeddingSource,
    pinecone_vector_id: str | None = None,
    source_title: str | None = None,
    source_url: str | None = None,
) -> UUID:
    """Insert a single text chunk reference with its Pinecone vector ID."""
    row = await conn.fetchrow(
        """
        INSERT INTO public.embeddings
            (generation_job_id, map_id, chunk_text, pinecone_vector_id, embedding_model, source, source_title, source_url)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING id
        """,
        generation_job_id,
        map_id,
        chunk_text,
        pinecone_vector_id,
        embedding_model,
        source.value,
        source_title,
        source_url,
    )
    return row["id"]


async def mark_seed_asset_extracted(
    conn: asyncpg.Connection,
    asset_id: UUID,
    content_text: str,
    file_size_bytes: int | None = None,
    checksum_sha256: str | None = None,
) -> None:
    await conn.execute(
        """
        UPDATE public.seed_assets
        SET content_text = $2, file_size_bytes = COALESCE($3, file_size_bytes),
            checksum_sha256 = COALESCE($4, checksum_sha256)
        WHERE id = $1
        """,
        asset_id,
        content_text,
        file_size_bytes,
        checksum_sha256,
    )


async def get_map_embeddings(conn: asyncpg.Connection, map_id: UUID) -> list[EmbeddingRecord]:
    """Retrieve chunk references and Pinecone vector IDs for a mind map."""
    rows = await conn.fetch(
        """
        SELECT id, generation_job_id, map_id, chunk_text, pinecone_vector_id,
               embedding_model, source, source_title, source_url
        FROM public.embeddings
        WHERE map_id = $1
        ORDER BY created_at
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
    conn: asyncpg.Connection,
    *,
    map_id: UUID,
    query_embedding: list[float],
    query_text: str,
    top_k: int,
) -> list[EmbeddingRecord]:
    """[DEPRECATED] pgvector hybrid retrieval has been replaced by PineconeVectorStore in the worker."""
    raise NotImplementedError(
        "PostgreSQL pgvector hybrid retrieval is deprecated. Vector search is now executed via PineconeVectorStore in the worker."
    )


# ---------------------------------------------------------------------
# Optimistic Versioning & Graph Editing
# ---------------------------------------------------------------------


class OptimisticConcurrencyError(Exception):
    """Raised when an update fails because the map's current version does not match the expected version."""

    def __init__(self, current_version: int, expected_version: int) -> None:
        self.current_version = current_version
        self.expected_version = expected_version
        super().__init__(
            f"Optimistic lock conflict: map current version is {current_version}, but expected {expected_version}"
        )


async def get_map_version(conn: asyncpg.Connection, map_id: UUID) -> int:
    """Retrieve the current optimistic version of a mind map."""
    val = await conn.fetchval(
        "SELECT COALESCE(version, 1) FROM public.mind_maps WHERE id = $1",
        map_id,
    )
    return int(val) if val is not None else 1


async def check_and_bump_map_version(
    conn: asyncpg.Connection,
    map_id: UUID,
    expected_version: int | None = None,
) -> int:
    """Check optimistic version and increment atomically.

    Raises OptimisticConcurrencyError if expected_version is provided and does not match.
    """
    if expected_version is not None:
        new_v = await conn.fetchval(
            """
            UPDATE public.mind_maps
            SET version = COALESCE(version, 1) + 1, updated_at = NOW()
            WHERE id = $1 AND COALESCE(version, 1) = $2
            RETURNING version
            """,
            map_id,
            expected_version,
        )
        if new_v is None:
            actual_v = await conn.fetchval(
                "SELECT COALESCE(version, 1) FROM public.mind_maps WHERE id = $1",
                map_id,
            )
            if actual_v is None:
                raise ValueError("Map not found")
            raise OptimisticConcurrencyError(
                current_version=int(actual_v),
                expected_version=expected_version,
            )
        return int(new_v)

    new_v = await conn.fetchval(
        """
        UPDATE public.mind_maps
        SET version = COALESCE(version, 1) + 1, updated_at = NOW()
        WHERE id = $1
        RETURNING version
        """,
        map_id,
    )
    if new_v is None:
        raise ValueError("Map not found")
    return int(new_v)


# ---------------------------------------------------------------------
# Node CRUD Operations
# ---------------------------------------------------------------------


async def get_node_by_id(conn: asyncpg.Connection, map_id: UUID, node_id: UUID) -> dict | None:
    row = await conn.fetchrow(
        """
        SELECT id, map_id, label, description, pos_x, pos_y, background_color, text_color,
               width, height, node_type, source_job_id, node_data, created_at, updated_at
        FROM public.nodes
        WHERE id = $1 AND map_id = $2
        """,
        node_id,
        map_id,
    )
    return dict(row) if row else None


async def list_map_nodes(conn: asyncpg.Connection, map_id: UUID) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT id, map_id, label, description, pos_x, pos_y, background_color, text_color,
               width, height, node_type, source_job_id, node_data, created_at, updated_at
        FROM public.nodes
        WHERE map_id = $1
        ORDER BY created_at ASC
        """,
        map_id,
    )
    return [dict(r) for r in rows]


async def create_map_node(
    conn: asyncpg.Connection,
    *,
    node_id: UUID,
    map_id: UUID,
    label: str,
    description: str | None = None,
    pos_x: float = 0.0,
    pos_y: float = 0.0,
    background_color: str | None = None,
    text_color: str | None = None,
    width: float | None = None,
    height: float | None = None,
    node_type: str = "default",
    source_job_id: UUID | None = None,
    node_data: dict | None = None,
) -> dict:
    row = await conn.fetchrow(
        """
        INSERT INTO public.nodes
            (id, map_id, label, description, pos_x, pos_y,
             background_color, text_color, width, height, node_type, source_job_id, node_data)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
        RETURNING id, map_id, label, description, pos_x, pos_y,
                  background_color, text_color, width, height, node_type,
                  source_job_id, node_data, created_at, updated_at
        """,
        node_id,
        map_id,
        label,
        description,
        pos_x,
        pos_y,
        background_color,
        text_color,
        width,
        height,
        node_type,
        source_job_id,
        node_data,
    )
    return dict(row)


async def update_map_node(
    conn: asyncpg.Connection,
    map_id: UUID,
    node_id: UUID,
    updates: dict[str, Any],
) -> dict | None:
    if not updates:
        return await get_node_by_id(conn, map_id, node_id)

    parts: list[str] = []
    args: list[Any] = [node_id, map_id]

    allowed_cols = {
        "label",
        "description",
        "pos_x",
        "pos_y",
        "background_color",
        "text_color",
        "width",
        "height",
        "node_type",
        "node_data",
    }

    for col, val in updates.items():
        if col in allowed_cols:
            args.append(val)
            parts.append(f"{col} = ${len(args)}")

    if not parts:
        return await get_node_by_id(conn, map_id, node_id)

    parts.append("updated_at = NOW()")
    sql = f"""
        UPDATE public.nodes
        SET {", ".join(parts)}
        WHERE id = $1 AND map_id = $2
        RETURNING id, map_id, label, description, pos_x, pos_y,
                  background_color, text_color, width, height, node_type,
                  source_job_id, node_data, created_at, updated_at
    """
    row = await conn.fetchrow(sql, *args)
    return dict(row) if row else None


async def delete_map_node(
    conn: asyncpg.Connection,
    map_id: UUID,
    node_id: UUID,
    node_ids_to_delete: set[UUID] | None = None,
) -> list[UUID]:
    targets = list(node_ids_to_delete) if node_ids_to_delete else [node_id]
    await conn.execute(
        "DELETE FROM public.nodes WHERE map_id = $1 AND id = ANY($2::uuid[])",
        map_id,
        targets,
    )
    return targets


# ---------------------------------------------------------------------
# Edge CRUD Operations
# ---------------------------------------------------------------------


async def get_edge_by_id(conn: asyncpg.Connection, map_id: UUID, edge_id: UUID) -> dict | None:
    row = await conn.fetchrow(
        """
        SELECT id, map_id, source_node_id, target_node_id, label, edge_data, created_at
        FROM public.edges
        WHERE id = $1 AND map_id = $2
        """,
        edge_id,
        map_id,
    )
    return dict(row) if row else None


async def list_map_edges(conn: asyncpg.Connection, map_id: UUID) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT id, map_id, source_node_id, target_node_id, label, edge_data, created_at
        FROM public.edges
        WHERE map_id = $1
        ORDER BY created_at ASC
        """,
        map_id,
    )
    return [dict(r) for r in rows]


async def create_map_edge(
    conn: asyncpg.Connection,
    *,
    edge_id: UUID,
    map_id: UUID,
    source_node_id: UUID,
    target_node_id: UUID,
    label: str | None = None,
    edge_data: dict | None = None,
) -> dict:
    row = await conn.fetchrow(
        """
        INSERT INTO public.edges (id, map_id, source_node_id, target_node_id, label, edge_data)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id, map_id, source_node_id, target_node_id, label, edge_data, created_at
        """,
        edge_id,
        map_id,
        source_node_id,
        target_node_id,
        label,
        edge_data,
    )
    return dict(row)


async def update_map_edge(
    conn: asyncpg.Connection,
    map_id: UUID,
    edge_id: UUID,
    updates: dict[str, Any],
) -> dict | None:
    if not updates:
        return await get_edge_by_id(conn, map_id, edge_id)

    parts: list[str] = []
    args: list[Any] = [edge_id, map_id]

    if "label" in updates:
        args.append(updates["label"])
        parts.append(f"label = ${len(args)}")

    if "edge_data" in updates:
        args.append(updates["edge_data"])
        parts.append(f"edge_data = ${len(args)}")

    if not parts:
        return await get_edge_by_id(conn, map_id, edge_id)

    sql = f"""
        UPDATE public.edges
        SET {", ".join(parts)}
        WHERE id = $1 AND map_id = $2
        RETURNING id, map_id, source_node_id, target_node_id, label, edge_data, created_at
    """
    row = await conn.fetchrow(sql, *args)
    return dict(row) if row else None


async def delete_map_edge(conn: asyncpg.Connection, map_id: UUID, edge_id: UUID) -> bool:
    res = await conn.execute(
        "DELETE FROM public.edges WHERE id = $1 AND map_id = $2",
        edge_id,
        map_id,
    )
    return bool(res.endswith("1"))


# ---------------------------------------------------------------------
# S2-BE2: Node Comments
# ---------------------------------------------------------------------


async def create_node_comment(
    conn: asyncpg.Connection,
    *,
    map_id: UUID,
    node_id: UUID,
    user_id: UUID,
    content: str,
) -> dict:
    row = await conn.fetchrow(
        """
        INSERT INTO public.node_comments (map_id, node_id, user_id, content)
        VALUES ($1, $2, $3, $4)
        RETURNING id, map_id, node_id, user_id, content, created_at, updated_at
        """,
        map_id,
        node_id,
        user_id,
        content.strip(),
    )
    return dict(row)


async def list_node_comments(
    conn: asyncpg.Connection,
    map_id: UUID,
    node_id: UUID,
) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT id, map_id, node_id, user_id, content, created_at, updated_at
        FROM public.node_comments
        WHERE map_id = $1 AND node_id = $2
        ORDER BY created_at ASC
        """,
        map_id,
        node_id,
    )
    return [dict(r) for r in rows]


async def get_node_comment(
    conn: asyncpg.Connection,
    map_id: UUID,
    comment_id: UUID,
) -> dict | None:
    row = await conn.fetchrow(
        """
        SELECT id, map_id, node_id, user_id, content, created_at, updated_at
        FROM public.node_comments
        WHERE map_id = $1 AND id = $2
        """,
        map_id,
        comment_id,
    )
    return dict(row) if row else None


async def update_node_comment(
    conn: asyncpg.Connection,
    map_id: UUID,
    comment_id: UUID,
    content: str,
) -> dict | None:
    row = await conn.fetchrow(
        """
        UPDATE public.node_comments
        SET content = $3, updated_at = NOW()
        WHERE map_id = $1 AND id = $2
        RETURNING id, map_id, node_id, user_id, content, created_at, updated_at
        """,
        map_id,
        comment_id,
        content.strip(),
    )
    return dict(row) if row else None


async def delete_node_comment(
    conn: asyncpg.Connection,
    map_id: UUID,
    comment_id: UUID,
) -> bool:
    res = await conn.execute(
        "DELETE FROM public.node_comments WHERE map_id = $1 AND id = $2",
        map_id,
        comment_id,
    )
    return bool(res.endswith("1"))


# ---------------------------------------------------------------------
# S2-BE2: Snapshots & Branch Resolution
# ---------------------------------------------------------------------


async def create_map_snapshot(
    conn: asyncpg.Connection,
    *,
    map_id: UUID,
    trigger_job_id: UUID | None = None,
    target_node_id: UUID | None = None,
    reason: str = "pre_regeneration",
) -> UUID:
    """Save an immutable snapshot of nodes and edges before partial/full regeneration."""
    nodes = await list_map_nodes(conn, map_id)
    edges = await list_map_edges(conn, map_id)

    clean_nodes = [{k: str(v) if isinstance(v, (UUID, datetime)) else v for k, v in n.items()} for n in nodes]
    clean_edges = [{k: str(v) if isinstance(v, (UUID, datetime)) else v for k, v in e.items()} for e in edges]

    snapshot_id = await conn.fetchval(
        """
        INSERT INTO public.map_snapshots
            (map_id, trigger_job_id, target_node_id, reason, nodes_snapshot, edges_snapshot)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
        """,
        map_id,
        trigger_job_id,
        target_node_id,
        reason,
        clean_nodes,
        clean_edges,
    )
    return snapshot_id


async def list_map_snapshots(conn: asyncpg.Connection, map_id: UUID) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT id, map_id, trigger_job_id, target_node_id, reason,
               jsonb_array_length(nodes_snapshot) AS nodes_count,
               jsonb_array_length(edges_snapshot) AS edges_count,
               created_at
        FROM public.map_snapshots
        WHERE map_id = $1
        ORDER BY created_at DESC
        """,
        map_id,
    )
    return [dict(r) for r in rows]


async def get_map_snapshot(conn: asyncpg.Connection, map_id: UUID, snapshot_id: UUID) -> dict | None:
    row = await conn.fetchrow(
        """
        SELECT id, map_id, trigger_job_id, target_node_id, reason,
               nodes_snapshot, edges_snapshot, created_at
        FROM public.map_snapshots
        WHERE map_id = $1 AND id = $2
        """,
        map_id,
        snapshot_id,
    )
    return dict(row) if row else None


async def restore_map_snapshot(
    conn: asyncpg.Connection,
    *,
    map_id: UUID,
    snapshot_id: UUID,
) -> dict[str, Any]:
    """Restore mind map graph state to the specified snapshot.

    1. Retrieves the snapshot.
    2. Takes an automatic safety snapshot of current graph state before overwriting.
    3. Replaces all nodes and edges with snapshot data.
    4. Bumps map version atomically.
    """
    snapshot = await get_map_snapshot(conn, map_id, snapshot_id)
    if not snapshot:
        raise ValueError(f"Snapshot {snapshot_id} not found for map {map_id}")

    # Create safety snapshot of current state before restoring
    await create_map_snapshot(
        conn,
        map_id=map_id,
        reason=f"Safety snapshot before restoring snapshot {snapshot_id}",
    )

    await conn.execute("DELETE FROM public.edges WHERE map_id = $1", map_id)
    await conn.execute("DELETE FROM public.nodes WHERE map_id = $1", map_id)

    raw_nodes = snapshot.get("nodes_snapshot")
    raw_edges = snapshot.get("edges_snapshot")
    if raw_nodes is None and isinstance(snapshot.get("snapshot_data"), dict):
        raw_nodes = snapshot["snapshot_data"].get("nodes")
        raw_edges = snapshot["snapshot_data"].get("edges")

    if isinstance(raw_nodes, str):
        raw_nodes = json.loads(raw_nodes)
    if isinstance(raw_edges, str):
        raw_edges = json.loads(raw_edges)

    raw_nodes = raw_nodes or []
    raw_edges = raw_edges or []

    if raw_nodes:
        await conn.executemany(
            """
            INSERT INTO public.nodes
                (id, map_id, label, description, pos_x, pos_y,
                 background_color, text_color, width, height, node_type, source_job_id, node_data)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            """,
            [
                (
                    UUID(str(n["id"])),
                    map_id,
                    n["label"],
                    n.get("description"),
                    float(n.get("pos_x", 0.0)),
                    float(n.get("pos_y", 0.0)),
                    n.get("background_color"),
                    n.get("text_color"),
                    float(n["width"]) if n.get("width") is not None else None,
                    float(n["height"]) if n.get("height") is not None else None,
                    n.get("node_type", "default"),
                    UUID(str(n["source_job_id"])) if n.get("source_job_id") else None,
                    n.get("node_data"),
                )
                for n in raw_nodes
            ],
        )

    if raw_edges:
        await conn.executemany(
            """
            INSERT INTO public.edges (id, map_id, source_node_id, target_node_id, label, edge_data)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            [
                (
                    UUID(str(e["id"])),
                    map_id,
                    UUID(str(e.get("source_node_id") or e.get("source"))),
                    UUID(str(e.get("target_node_id") or e.get("target"))),
                    e.get("label"),
                    e.get("edge_data"),
                )
                for e in raw_edges
            ],
        )

    new_version = await check_and_bump_map_version(conn, map_id)
    return {
        "status": "restored",
        "map_id": map_id,
        "snapshot_id": snapshot_id,
        "version": new_version,
        "nodes_restored": len(raw_nodes),
        "edges_restored": len(raw_edges),
        "snapshot_data": {"nodes": raw_nodes, "edges": raw_edges},
    }


async def delete_map_snapshot(conn: asyncpg.Connection, *, map_id: UUID, snapshot_id: UUID) -> bool:
    """Delete an immutable snapshot of a mind map."""
    res = await conn.execute(
        "DELETE FROM public.map_snapshots WHERE map_id = $1 AND id = $2",
        map_id,
        snapshot_id,
    )
    return res == "DELETE 1" or (isinstance(res, str) and res.startswith("DELETE ") and res != "DELETE 0")


async def has_active_node_job(conn: asyncpg.Connection, map_id: UUID, node_id: UUID) -> bool:
    """Check if an active node regeneration job is already pending or running for this node."""
    val = await conn.fetchval(
        """
        SELECT 1 FROM public.generation_jobs
        WHERE map_id = $1 AND target_node_id = $2
          AND job_type = 'regenerate_node'
          AND status IN ('pending', 'running')
        LIMIT 1
        """,
        map_id,
        node_id,
    )
    return bool(val)


async def replace_node_branch(
    conn: asyncpg.Connection,
    *,
    map_id: UUID,
    target_node_id: UUID,
    source_job_id: UUID,
    new_nodes: list[NodeRecord],
    new_edges: list[EdgeRecord],
) -> None:
    """Regenerate only affected descendants while preserving the rest of the mind map.

    1. Identifies all existing descendants downstream of target_node_id.
    2. Removes old descendant nodes (and their connecting edges).
    3. Preserves existing incoming edges into target_node_id from its parent.
    4. Inserts new branch nodes and edges.
    5. Updates map version and timestamp.
    """
    edges = await list_map_edges(conn, map_id)
    from maps.graph_validator import get_node_descendant_ids

    descendant_ids = get_node_descendant_ids(edges, target_node_id)

    # Delete existing descendant nodes (PostgreSQL CASCADE will drop their internal edges)
    if descendant_ids:
        await conn.execute(
            "DELETE FROM public.nodes WHERE map_id = $1 AND id = ANY($2::uuid[])",
            map_id,
            list(descendant_ids),
        )

    # Delete any outgoing edges that originated from target_node_id
    await conn.execute(
        "DELETE FROM public.edges WHERE map_id = $1 AND source_node_id = $2",
        map_id,
        target_node_id,
    )

    # Insert newly generated branch nodes (skip target_node_id if already present in new_nodes)
    branch_nodes_to_insert = [n for n in new_nodes if n.id != target_node_id]
    if branch_nodes_to_insert:
        await conn.executemany(
            """
            INSERT INTO public.nodes
                (id, map_id, label, description, pos_x, pos_y,
                 background_color, text_color, node_type, source_job_id, node_data)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            [
                (
                    n.id,
                    map_id,
                    n.label,
                    n.description,
                    n.pos_x,
                    n.pos_y,
                    n.background_color,
                    n.text_color,
                    n.node_type,
                    source_job_id,
                    n.node_data,
                )
                for n in branch_nodes_to_insert
            ],
        )

    # If the generator updated target_node itself (e.g. title or description refinement)
    target_node_record = next((n for n in new_nodes if n.id == target_node_id), None)
    if target_node_record:
        await conn.execute(
            """
            UPDATE public.nodes
            SET label = COALESCE($3, label),
                description = COALESCE($4, description),
                source_job_id = $5,
                updated_at = NOW()
            WHERE id = $1 AND map_id = $2
            """,
            target_node_id,
            map_id,
            target_node_record.label,
            target_node_record.description,
            source_job_id,
        )

    # Insert newly generated edges
    if new_edges:
        await conn.executemany(
            """
            INSERT INTO public.edges (id, map_id, source_node_id, target_node_id, label, edge_data)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            [(e.id, map_id, e.source_node_id, e.target_node_id, e.label, e.edge_data) for e in new_edges],
        )

    # Bump map version atomically
    await check_and_bump_map_version(conn, map_id)
