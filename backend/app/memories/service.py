"""Database operations for the S3-BE4 Memory API.

All queries run against public.agent_memories using the asyncpg pool.
The table schema (uuid PK, user_id, map_id, memory_type, content,
is_active, created_at) was added by migration
20261114000300_s2_db3_chat_memory.sql which is already on main.
"""

from __future__ import annotations

import contextlib
import logging
import os
from datetime import UTC, datetime
from uuid import UUID

from core.config import settings
from memories.schemas import (
    MemoryPatchRequest,
    MemoryProposeRequest,
    MemoryProposeResponse,
)

logger = logging.getLogger(__name__)

# Configurable per-user limit (S3-BE4: start at 500 rows)
MEMORY_PER_USER_LIMIT: int = getattr(
    settings, "MEMORY_PER_USER_LIMIT", int(os.environ.get("MEMORY_PER_USER_LIMIT", "500"))
)

# Exact-duplicate threshold: identical content after strip+lower
_DEDUP_EXACT = True

# Near-duplicate Jaccard token overlap threshold
_JACCARD_THRESHOLD = 0.80


def _jaccard(a: str, b: str) -> float:
    ta = set(a.lower().split())
    tb = set(b.lower().split())
    if not ta and not tb:
        return 1.0
    return len(ta & tb) / len(ta | tb)


def _row_to_item(row: dict) -> dict:
    kind = row.get("kind") or row.get("memory_type") or "fact"
    if kind not in ("preference", "decision", "fact"):
        kind = "fact"
    memory_type = row.get("memory_type") or kind
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "map_id": row.get("map_id"),
        "kind": kind,
        "memory_type": memory_type,
        "content": row["content"],
        "importance": float(row["importance"]) if row.get("importance") is not None else 1.0,
        "is_active": row.get("is_active", True),
        "disabled_at": row.get("disabled_at"),
        "last_accessed_at": row.get("last_accessed_at"),
        "scope": "map" if row.get("map_id") else "global",
        "created_at": row["created_at"],
        "updated_at": row.get("updated_at"),
    }


# ---------------------------------------------------------------------------
# List (GET /memories) — paginated, optionally scoped to map_id
# ---------------------------------------------------------------------------


async def list_memories(
    pool,
    *,
    user_id: UUID,
    map_id: UUID | None = None,
    is_active: bool | None = None,
    include_global: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Return (items, total) for user, optionally filtered by map_id / is_active."""
    conditions = ["user_id = $1"]
    args: list = [user_id]
    idx = 2

    if map_id is not None:
        scope = f"map_id = ${idx}"
        conditions.append(f"({scope} OR map_id IS NULL)" if include_global else scope)
        args.append(map_id)
        idx += 1

    if is_active is not None:
        if is_active:
            conditions.append("(is_active = true AND (disabled_at IS NULL OR disabled_at > now()))")
        else:
            conditions.append("(is_active = false OR disabled_at IS NOT NULL)")

    where = " AND ".join(conditions)

    count_q = f"SELECT COUNT(*) FROM public.agent_memories WHERE {where}"
    total: int = await pool.fetchval(count_q, *args)

    data_q = (
        f"SELECT id, user_id, map_id, kind, memory_type, content, importance, is_active, disabled_at, last_accessed_at, created_at, updated_at "
        f"FROM public.agent_memories WHERE {where} "
        f"ORDER BY importance DESC, created_at DESC, id DESC "
        f"LIMIT ${idx} OFFSET ${idx + 1}"
    )
    rows = await pool.fetch(data_q, *args, limit, offset)
    items = [_row_to_item(dict(r)) for r in rows]

    # Asynchronously bump last_accessed_at for retrieved items
    if items:
        try:
            ids = [item["id"] for item in items]
            await pool.execute(
                "UPDATE public.agent_memories SET last_accessed_at = now() WHERE id = ANY($1::uuid[])",
                ids,
            )
        except Exception as exc:
            logger.debug("Failed to bump last_accessed_at: %s", exc)

    return items, total


# ---------------------------------------------------------------------------
# Get single memory (ownership enforced)
# ---------------------------------------------------------------------------


async def get_memory(pool, *, memory_id: UUID, user_id: UUID) -> dict | None:
    row = await pool.fetchrow(
        "SELECT id, user_id, map_id, kind, memory_type, content, importance, is_active, disabled_at, last_accessed_at, created_at, updated_at "
        "FROM public.agent_memories WHERE id = $1 AND user_id = $2",
        memory_id,
        user_id,
    )
    if not row:
        return None
    with contextlib.suppress(Exception):
        await pool.execute(
            "UPDATE public.agent_memories SET last_accessed_at = now() WHERE id = $1",
            memory_id,
        )
    return _row_to_item(dict(row))


# ---------------------------------------------------------------------------
# PATCH /memories/{id}
# ---------------------------------------------------------------------------


async def patch_memory(pool, *, memory_id: UUID, user_id: UUID, payload: MemoryPatchRequest) -> dict | None:
    """Update content, active state, kind, or importance. Returns updated row or None if not found."""
    sets: list[str] = []
    args: list = []
    idx = 1

    if payload.content is not None:
        sets.append(f"content = ${idx}")
        args.append(payload.content.strip())
        idx += 1

    if payload.is_active is not None:
        sets.append(f"is_active = ${idx}")
        args.append(payload.is_active)
        idx += 1
        if payload.is_active is False:
            sets.append("disabled_at = now()")
        else:
            sets.append("disabled_at = NULL")

    if payload.kind is not None:
        sets.append(f"kind = ${idx}")
        args.append(payload.kind)
        idx += 1
        sets.append(f"memory_type = ${idx}")
        args.append(payload.kind)
        idx += 1
    elif payload.memory_type is not None:
        clean_type = payload.memory_type.strip()
        sets.append(f"memory_type = ${idx}")
        args.append(clean_type)
        idx += 1
        sets.append(f"kind = ${idx}")
        args.append(clean_type if clean_type in ("preference", "decision", "fact") else "fact")
        idx += 1

    if payload.importance is not None:
        sets.append(f"importance = ${idx}")
        args.append(payload.importance)
        idx += 1

    if not sets:
        return await get_memory(pool, memory_id=memory_id, user_id=user_id)

    sets.append("updated_at = now()")

    # Append ownership filter args
    args.extend([memory_id, user_id])
    set_clause = ", ".join(sets)
    q = (
        f"UPDATE public.agent_memories SET {set_clause} "
        f"WHERE id = ${idx} AND user_id = ${idx + 1} "
        f"RETURNING id, user_id, map_id, kind, memory_type, content, importance, is_active, disabled_at, last_accessed_at, created_at, updated_at"
    )
    row = await pool.fetchrow(q, *args)
    return _row_to_item(dict(row)) if row else None


# ---------------------------------------------------------------------------
# DELETE /memories/{id}
# ---------------------------------------------------------------------------


async def delete_memory(pool, *, memory_id: UUID, user_id: UUID) -> bool:
    """Hard-delete the row. Returns True if a row was deleted."""
    result = await pool.execute(
        "DELETE FROM public.agent_memories WHERE id = $1 AND user_id = $2",
        memory_id,
        user_id,
    )
    # asyncpg returns "DELETE N" string
    return result.endswith("1")


# ---------------------------------------------------------------------------
# POST /internal/memory/propose — worker write-path (S3-BE4)
# ---------------------------------------------------------------------------


async def _propose_memory_locked(conn, payload: MemoryProposeRequest) -> MemoryProposeResponse:
    """Validate, deduplicate, enforce per-user limit, and store a memory within locked context."""
    user_id = payload.user_id
    content = payload.content.strip()
    if not content:
        return MemoryProposeResponse(stored=False, reason="empty_content")

    # 0. If map_id is provided, verify map existence and ownership
    if payload.map_id is not None:
        map_owner = await conn.fetchval(
            "SELECT user_id FROM public.mind_maps WHERE id = $1",
            payload.map_id,
        )
        if map_owner is None:
            raise ValueError(f"Map {payload.map_id} not found.")
        if map_owner != user_id:
            raise PermissionError("User does not own the specified map.")

    # 1. Per-user count limit
    count: int = await conn.fetchval(
        "SELECT COUNT(*) FROM public.agent_memories WHERE user_id = $1",
        user_id,
    )
    if count >= MEMORY_PER_USER_LIMIT:
        logger.info("Memory limit reached for user %s (%d/%d)", user_id, count, MEMORY_PER_USER_LIMIT)
        return MemoryProposeResponse(stored=False, reason="limit_exceeded")

    # 2. Exact duplicate check (same user, same content lowercased)
    exact = await conn.fetchval(
        "SELECT id FROM public.agent_memories "
        "WHERE user_id = $1 AND lower(btrim(content)) = lower($2) AND is_active = true "
        "LIMIT 1",
        user_id,
        content,
    )
    if exact is not None:
        return MemoryProposeResponse(stored=False, reason="duplicate", id=exact)

    # 3. Near-duplicate check (Jaccard on recent 100 active memories)
    rows = await conn.fetch(
        "SELECT id, content FROM public.agent_memories "
        "WHERE user_id = $1 AND is_active = true "
        "ORDER BY created_at DESC LIMIT 100",
        user_id,
    )
    for row in rows:
        sim = _jaccard(content, row["content"])
        if sim >= _JACCARD_THRESHOLD:
            logger.debug("Near-duplicate memory for user %s (%.2f similarity)", user_id, sim)
            return MemoryProposeResponse(stored=False, reason="near_duplicate", id=row["id"])

    # 4. Store
    raw_kind = payload.kind or payload.memory_type or "fact"
    kind = raw_kind if raw_kind in ("preference", "decision", "fact") else "fact"
    memory_type = payload.memory_type or kind
    importance = float(payload.importance) if payload.importance is not None else 1.0

    new_row = await conn.fetchrow(
        "INSERT INTO public.agent_memories (user_id, map_id, kind, memory_type, content, importance, is_active, last_accessed_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, true, now()) "
        "RETURNING id",
        user_id,
        payload.map_id,
        kind,
        memory_type,
        content,
        importance,
    )
    return MemoryProposeResponse(stored=True, reason="created", id=new_row["id"])


async def propose_memory(pool, payload: MemoryProposeRequest) -> MemoryProposeResponse:
    """Validate, deduplicate, enforce per-user limit, and store a memory safely with advisory locks (NEW-07)."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext('mem_propose:' || $1::text))",
            str(payload.user_id),
        )
        return await _propose_memory_locked(conn, payload)


# ---------------------------------------------------------------------------
# GDPR export — GET /memories/export
# ---------------------------------------------------------------------------


async def gdpr_export(pool, *, user_id: UUID) -> dict:
    rows = await pool.fetch(
        "SELECT id, map_id, kind, memory_type, content, importance, is_active, disabled_at, last_accessed_at, created_at "
        "FROM public.agent_memories WHERE user_id = $1 ORDER BY created_at DESC",
        user_id,
    )
    memories = [
        {
            "id": str(r["id"]),
            "map_id": str(r["map_id"]) if r["map_id"] else None,
            "kind": r.get("kind") or r.get("memory_type") or "fact",
            "memory_type": r["memory_type"],
            "content": r["content"],
            "importance": float(r["importance"]) if r.get("importance") is not None else 1.0,
            "is_active": r["is_active"],
            "disabled_at": r["disabled_at"].isoformat() if r.get("disabled_at") else None,
            "last_accessed_at": r["last_accessed_at"].isoformat() if r.get("last_accessed_at") else None,
            "scope": "map" if r.get("map_id") else "global",
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]
    return {
        "user_id": str(user_id),
        "exported_at": datetime.now(UTC).isoformat(),
        "total_memories": len(memories),
        "memories": memories,
        "notice": (
            "GDPR Article 15 (Right of Access) and Article 20 (Data Portability) export. "
            "This bundle contains all agent memory records associated with your account."
        ),
    }
