"""
Backend repository handling all database interactions with PostgreSQL / Supabase.
All SQL operations are centralized here.
"""

from __future__ import annotations

import json
import logging
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

logger = logging.getLogger(__name__)


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
    row = await conn.fetchrow(
        "INSERT INTO public.mind_maps (user_id, title) VALUES ($1, $2) "
        "RETURNING id, title, description, is_public, created_at, updated_at",
        user_id,
        title.strip(),
    )
    return dict(row)


async def list_mind_maps(conn: asyncpg.Connection, user_id: UUID) -> list[dict]:
    rows = await conn.fetch(
        "SELECT id, title, description, is_public, created_at, updated_at FROM public.mind_maps "
        "WHERE user_id = $1 ORDER BY updated_at DESC",
        user_id,
    )
    return [dict(r) for r in rows]


async def get_mind_map(conn: asyncpg.Connection, map_id: UUID) -> dict | None:
    row = await conn.fetchrow(
        "SELECT id, user_id, title, description, is_public, created_at, updated_at FROM public.mind_maps WHERE id = $1",
        map_id,
    )
    return dict(row) if row else None


async def assert_map_owner(conn: asyncpg.Connection, map_id: UUID, user_id: UUID) -> bool:
    ok = await conn.fetchval("SELECT 1 FROM public.mind_maps WHERE id = $1 AND user_id = $2", map_id, user_id)
    return bool(ok)


# ---------------------------------------------------------------------
# Search & Concurrency Control (OCC)
# ---------------------------------------------------------------------

SORT_COLUMNS = {
    "updated_at_desc": "m.updated_at DESC",
    "-updated_at": "m.updated_at DESC",
    "updated_at_asc": "m.updated_at ASC",
    "updated_at": "m.updated_at ASC",
    "created_at_desc": "m.created_at DESC",
    "-created_at": "m.created_at DESC",
    "created_at_asc": "m.created_at ASC",
    "created_at": "m.created_at ASC",
    "title_asc": "m.title ASC",
    "title": "m.title ASC",
    "title_desc": "m.title DESC",
    "-title": "m.title DESC",
}


async def search_maps(
    conn: asyncpg.Connection,
    *,
    user_id: UUID,
    query: str | None = None,
    sort: str = "updated_at_desc",
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """
    Search mind maps by querying:
      1. Map titles (m.title ILIKE %q%)
      2. Map descriptions (m.description ILIKE %q%)
      3. Node labels (EXISTS in public.nodes n WHERE n.label ILIKE %q%)

    Returns (items, total_count).
    Does NOT modify the database schema.
    """
    clean_query = (query or "").strip()
    order_clause = SORT_COLUMNS.get(sort.lower().strip(), "m.updated_at DESC")

    filter_conditions = ["m.user_id = $1"]
    args: list[Any] = [user_id]

    if clean_query:
        like_pattern = f"%{clean_query}%"
        args.append(like_pattern)
        filter_conditions.append(
            f"""(
                m.title ILIKE ${len(args)}
                OR (m.description IS NOT NULL AND m.description ILIKE ${len(args)})
                OR EXISTS (
                    SELECT 1 FROM public.nodes n
                    WHERE n.map_id = m.id AND n.label ILIKE ${len(args)}
                )
            )"""
        )

    where_clause = " AND ".join(filter_conditions)

    # 1. Total matching count
    count_sql = f"SELECT count(*)::int FROM public.mind_maps m WHERE {where_clause}"
    total = await conn.fetchval(count_sql, *args) or 0

    # 2. Paginated rows with metadata
    data_args = list(args)
    limit_idx = len(data_args) + 1
    offset_idx = len(data_args) + 2
    data_args.extend([limit, offset])

    data_sql = f"""
        SELECT m.id, m.title, m.description, m.is_public, m.created_at, m.updated_at,
               (SELECT count(*)::int FROM public.nodes n WHERE n.map_id = m.id) AS node_count,
               (SELECT j.status FROM public.generation_jobs j WHERE j.map_id = m.id ORDER BY j.created_at DESC LIMIT 1) AS latest_job_status,
               (SELECT j.progress_percent::int FROM public.generation_jobs j WHERE j.map_id = m.id ORDER BY j.created_at DESC LIMIT 1) AS latest_job_progress,
               (SELECT j.id FROM public.generation_jobs j WHERE j.map_id = m.id ORDER BY j.created_at DESC LIMIT 1) AS latest_job_id
        FROM public.mind_maps m
        WHERE {where_clause}
        ORDER BY {order_clause}
        LIMIT ${limit_idx} OFFSET ${offset_idx}
    """

    rows = await conn.fetch(data_sql, *data_args)
    return [dict(r) for r in rows], total


async def update_mind_map_occ(
    conn: asyncpg.Connection,
    *,
    map_id: UUID,
    user_id: UUID,
    expected_updated_at: datetime,
    title: str | None = None,
    description: str | None = None,
    is_public: bool | None = None,
) -> tuple[dict | None, str | None]:
    """
    Update mind map metadata using Optimistic Concurrency Control (OCC).
    Matches both map_id and expected_updated_at atomically.

    Returns:
      (updated_map_dict, None)        -> Success (200)
      (None, "NOT_FOUND")            -> Map does not exist or not owned by user (404)
      (None, "CONFLICT")             -> Map modified by another transaction (409)
    """
    updates = []
    args: list[Any] = [map_id, user_id]

    if title is not None:
        args.append(title.strip())
        updates.append(f"title = ${len(args)}")
    if description is not None:
        args.append(description)
        updates.append(f"description = ${len(args)}")
    if is_public is not None:
        args.append(is_public)
        updates.append(f"is_public = ${len(args)}")

    if not updates:
        # No actual fields changed, but verify existence and version
        current_map = await conn.fetchrow(
            "SELECT id, title, description, is_public, created_at, updated_at FROM public.mind_maps WHERE id = $1 AND user_id = $2",
            map_id,
            user_id,
        )
        if not current_map:
            return None, "NOT_FOUND"
        if current_map["updated_at"] != expected_updated_at:
            return None, "CONFLICT"
        return dict(current_map), None

    # Append expected_updated_at to WHERE clause
    args.append(expected_updated_at)
    version_idx = len(args)

    # Note: trg_mind_maps_updated_at trigger will update updated_at automatically
    sql = f"""
        UPDATE public.mind_maps
        SET {", ".join(updates)}
        WHERE id = $1 AND user_id = $2 AND updated_at = ${version_idx}
        RETURNING id, title, description, is_public, created_at, updated_at
    """
    row = await conn.fetchrow(sql, *args)

    if row:
        return dict(row), None

    # If 0 rows updated, disambiguate between NOT_FOUND and CONFLICT
    existing_updated_at = await conn.fetchval(
        "SELECT updated_at FROM public.mind_maps WHERE id = $1 AND user_id = $2",
        map_id,
        user_id,
    )
    if existing_updated_at is None:
        return None, "NOT_FOUND"

    return None, "CONFLICT"


# ---------------------------------------------------------------------
# Chat Threads & Messages
# ---------------------------------------------------------------------


async def get_or_create_chat_thread(conn: asyncpg.Connection, map_id: UUID, user_id: UUID) -> UUID:
    row = await conn.fetchrow("SELECT id FROM public.chat_threads WHERE map_id = $1", map_id)
    if row:
        return row["id"]
    row = await conn.fetchrow(
        "INSERT INTO public.chat_threads (map_id, user_id) VALUES ($1, $2) RETURNING id",
        map_id,
        user_id,
    )
    return row["id"]


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
# Map Snapshots / Version History (US-04)
# ---------------------------------------------------------------------


async def next_snapshot_version(conn: asyncpg.Connection, map_id: UUID) -> int:
    """Return the next snapshot version number for a map (1-based)."""
    row = await conn.fetchrow(
        "SELECT COALESCE(MAX(version_number), 0) + 1 AS next_v FROM public.map_snapshots WHERE map_id = $1",
        map_id,
    )
    return int(row["next_v"])


async def create_map_snapshot(
    conn: asyncpg.Connection,
    *,
    map_id: UUID,
    trigger_source: str,
    trigger_job_id: UUID | None,
    snapshot_data: dict,
    label: str | None = None,
    created_by: UUID | None = None,
) -> dict | None:
    """Insert a new snapshot row for a map, returning the inserted row."""
    version = await next_snapshot_version(conn, map_id)
    row = await conn.fetchrow(
        """
        INSERT INTO public.map_snapshots
            (map_id, version_number, trigger_source, trigger_job_id,
             label, snapshot_data, created_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id, map_id, version_number, trigger_source, trigger_job_id,
                  label, snapshot_data, created_by, created_at
        """,
        map_id,
        version,
        trigger_source,
        trigger_job_id,
        label,
        asyncpg.Json(snapshot_data),
        created_by,
    )
    return dict(row) if row else None


async def list_map_snapshots(conn: asyncpg.Connection, map_id: UUID) -> list[dict]:
    """List all snapshots for a map, newest first (with snapshot_data payload)."""
    rows = await conn.fetch(
        """
        SELECT id, map_id, version_number, trigger_source, trigger_job_id,
               label, snapshot_data, created_by, created_at
        FROM public.map_snapshots WHERE map_id = $1
        ORDER BY version_number DESC
        """,
        map_id,
    )
    return [dict(r) for r in rows]


async def get_map_snapshot(conn: asyncpg.Connection, map_id: UUID, snapshot_id: UUID) -> dict | None:
    """Get a single snapshot including its snapshot_data payload."""
    row = await conn.fetchrow(
        """
        SELECT id, map_id, version_number, trigger_source, trigger_job_id,
               label, snapshot_data, created_by, created_at
        FROM public.map_snapshots WHERE id = $1 AND map_id = $2
        """,
        snapshot_id,
        map_id,
    )
    return dict(row) if row else None


async def restore_map_graph_from_snapshot(
    conn: asyncpg.Connection,
    *,
    map_id: UUID,
    snapshot_id: UUID,
) -> dict | None:
    """Atomically replace a map's nodes/edges with a snapshot's stored graph."""
    snap = await get_map_snapshot(conn, map_id, snapshot_id)
    if not snap:
        return None

    snapshot_data = snap["snapshot_data"]
    if isinstance(snapshot_data, str):
        snapshot_data = json.loads(snapshot_data)

    nodes_raw = snapshot_data.get("nodes", [])
    edges_raw = snapshot_data.get("edges", [])

    await conn.execute("DELETE FROM public.edges WHERE map_id = $1", map_id)
    await conn.execute("DELETE FROM public.nodes WHERE map_id = $1", map_id)

    if nodes_raw:
        await conn.executemany(
            """
            INSERT INTO public.nodes
                (id, map_id, label, description, pos_x, pos_y,
                 background_color, text_color, node_type, source_job_id, node_data)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            [
                (
                    n["id"],
                    map_id,
                    n.get("label"),
                    n.get("description"),
                    float(n.get("pos_x", 0.0)),
                    float(n.get("pos_y", 0.0)),
                    n.get("background_color"),
                    n.get("text_color"),
                    n.get("node_type", "default"),
                    n.get("source_job_id"),
                    n.get("node_data"),
                )
                for n in nodes_raw
            ],
        )
    if edges_raw:
        await conn.executemany(
            """
            INSERT INTO public.edges (id, map_id, source_node_id, target_node_id, label, edge_data)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            [
                (
                    e["id"],
                    map_id,
                    e.get("source_node_id") or e.get("source"),
                    e.get("target_node_id") or e.get("target"),
                    e.get("label"),
                    e.get("edge_data"),
                )
                for e in edges_raw
            ],
        )

    return snapshot_data


async def delete_map_snapshot(conn: asyncpg.Connection, map_id: UUID, snapshot_id: UUID) -> bool:
    """Delete a snapshot row by id and map_id. Returns True if deleted."""
    result = await conn.execute(
        "DELETE FROM public.map_snapshots WHERE id = $1 AND map_id = $2",
        snapshot_id,
        map_id,
    )
    return result.endswith(" 1")


# ---------------------------------------------------------------------
# Seed Assets & Embeddings
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
    raise NotImplementedError(
        "PostgreSQL pgvector hybrid retrieval is deprecated. Vector search is now executed via PineconeVectorStore in the worker."
    )
