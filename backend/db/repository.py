"""
Backend repository handling all database interactions with PostgreSQL / Supabase.
All SQL operations are centralized here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional
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
        callback_token_hash=row["callback_token_hash"] if "callback_token_hash" in row else None,
    )


# ---------------------------------------------------------------------
# Mind Maps & Ownership
# ---------------------------------------------------------------------

async def create_mind_map(conn: asyncpg.Connection, user_id: UUID, title: str = "Untitled mind map") -> dict:
    row = await conn.fetchrow(
        "INSERT INTO public.mind_maps (user_id, title) VALUES ($1, $2) "
        "RETURNING id, title, is_public, created_at, updated_at",
        user_id, title.strip(),
    )
    return dict(row)


async def list_mind_maps(conn: asyncpg.Connection, user_id: UUID) -> List[dict]:
    rows = await conn.fetch(
        "SELECT id, title, is_public, created_at, updated_at FROM public.mind_maps "
        "WHERE user_id = $1 ORDER BY updated_at DESC",
        user_id,
    )
    return [dict(r) for r in rows]


async def get_mind_map(conn: asyncpg.Connection, map_id: UUID) -> Optional[dict]:
    row = await conn.fetchrow(
        "SELECT id, user_id, title, is_public, created_at, updated_at FROM public.mind_maps WHERE id = $1",
        map_id,
    )
    return dict(row) if row else None


async def assert_map_owner(conn: asyncpg.Connection, map_id: UUID, user_id: UUID) -> bool:
    ok = await conn.fetchval(
        "SELECT 1 FROM public.mind_maps WHERE id = $1 AND user_id = $2", map_id, user_id
    )
    return bool(ok)


# ---------------------------------------------------------------------
# Chat Threads & Messages
# ---------------------------------------------------------------------

async def get_or_create_chat_thread(conn: asyncpg.Connection, map_id: UUID, user_id: UUID) -> UUID:
    row = await conn.fetchrow("SELECT id FROM public.chat_threads WHERE map_id = $1", map_id)
    if row:
        return row["id"]
    row = await conn.fetchrow(
        "INSERT INTO public.chat_threads (map_id, user_id) VALUES ($1, $2) RETURNING id",
        map_id, user_id,
    )
    return row["id"]


async def insert_chat_message(conn: asyncpg.Connection, thread_id: UUID, role: str, content: str) -> UUID:
    row = await conn.fetchrow(
        "INSERT INTO public.chat_messages (thread_id, role, content) VALUES ($1, $2, $3) RETURNING id",
        thread_id, role, content,
    )
    return row["id"]


async def get_chat_messages(conn: asyncpg.Connection, map_id: UUID) -> List[dict]:
    rows = await conn.fetch(
        """SELECT m.id, m.role, m.content, m.created_at
             FROM public.chat_messages m JOIN public.chat_threads t ON t.id = m.thread_id
            WHERE t.map_id = $1 ORDER BY m.created_at, m.id""",
        map_id,
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------
# Generation Jobs
# ---------------------------------------------------------------------

async def has_active_job(conn: asyncpg.Connection, map_id: UUID) -> bool:
    return bool(await conn.fetchval(
        """SELECT 1 FROM public.generation_jobs
            WHERE map_id = $1 AND job_type IN ('generate', 'regenerate_full')
              AND status IN ('pending', 'running') LIMIT 1""",
        map_id,
    ))


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
                job_id, map_id, triggered_by_message_id, job_type_str, target_node_id, callback_token_hash,
            )
        else:
            row = await conn.fetchrow(
                """
                INSERT INTO public.generation_jobs (map_id, triggered_by_message_id, job_type, target_node_id, callback_token_hash)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING *
                """,
                map_id, triggered_by_message_id, job_type_str, target_node_id, callback_token_hash,
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
        job_id, percent,
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
        job_id, error_message[:4000],
    )


async def mark_job_rejected(conn: asyncpg.Connection, job_id: UUID, error_message: str) -> None:
    await conn.execute(
        """
        UPDATE public.generation_jobs
        SET status = 'rejected', completed_at = NOW(), error_message = $2
        WHERE id = $1 AND status = 'pending'
        """,
        job_id, error_message[:4000],
    )



async def get_latest_job(conn: asyncpg.Connection, map_id: UUID) -> Optional[dict]:
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


async def get_job(conn: asyncpg.Connection, map_id: UUID, job_id: UUID) -> Optional[dict]:
    row = await conn.fetchrow(
        """SELECT id, map_id, job_type, status, progress_percent, error_message, created_at, started_at, completed_at
             FROM public.generation_jobs WHERE id = $1 AND map_id = $2""",
        job_id, map_id,
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
    source_job_id: Optional[UUID],
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
                    n.id, map_id, n.label, n.description, n.pos_x, n.pos_y,
                    n.background_color, n.text_color, n.node_type, source_job_id, n.node_data,
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
    source_job_id: Optional[UUID],
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
        generation_job_id, asset_type.value, file_name, storage_key, mime_type,
        content_text, file_size_bytes, checksum_sha256,
    )
    return SeedAsset(
        id=row["id"], generation_job_id=row["generation_job_id"],
        asset_type=SeedAssetType(row["asset_type"]), file_name=row["file_name"],
        storage_key=row["storage_key"], mime_type=row["mime_type"],
        content_text=row["content_text"], file_size_bytes=row["file_size_bytes"],
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
                r.generation_job_id, r.map_id, r.chunk_text, r.pinecone_vector_id, r.embedding_model,
                r.source.value, r.source_title, r.source_url,
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
        generation_job_id, map_id, chunk_text, pinecone_vector_id, embedding_model,
        source.value, source_title, source_url,
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
        asset_id, content_text, file_size_bytes, checksum_sha256,
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
            id=r["id"], generation_job_id=r["generation_job_id"], map_id=r["map_id"],
            chunk_text=r["chunk_text"], pinecone_vector_id=r["pinecone_vector_id"],
            embedding_model=r["embedding_model"], source=EmbeddingSource(r["source"]),
            source_title=r["source_title"], source_url=r["source_url"],
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


