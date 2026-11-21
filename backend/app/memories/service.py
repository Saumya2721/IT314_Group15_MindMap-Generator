"""Database operations for the S3-BE4 Memory API.

All queries run against public.agent_memories using the asyncpg pool.
The table schema (uuid PK, user_id, map_id, memory_type, content,
is_active, created_at) was added by migration
20261114000300_s2_db3_chat_memory.sql which is already on main.
"""

from __future__ import annotations

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
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "map_id": row.get("map_id"),
        "memory_type": row["memory_type"],
        "content": row["content"],
        "is_active": row["is_active"],
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
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Return (items, total) for user, optionally filtered by map_id / is_active."""
    conditions = ["user_id = $1"]
    args: list = [user_id]
    idx = 2

    if map_id is not None:
        conditions.append(f"map_id = ${idx}")
        args.append(map_id)
        idx += 1

    if is_active is not None:
        conditions.append(f"is_active = ${idx}")
        args.append(is_active)
        idx += 1

    where = " AND ".join(conditions)

    count_q = f"SELECT COUNT(*) FROM public.agent_memories WHERE {where}"
    total: int = await pool.fetchval(count_q, *args)

    data_q = (
        f"SELECT id, user_id, map_id, memory_type, content, is_active, created_at "
        f"FROM public.agent_memories WHERE {where} "
        f"ORDER BY created_at DESC "
        f"LIMIT ${idx} OFFSET ${idx + 1}"
    )
    rows = await pool.fetch(data_q, *args, limit, offset)
    items = [_row_to_item(dict(r)) for r in rows]
    return items, total


# ---------------------------------------------------------------------------
# Get single memory (ownership enforced)
# ---------------------------------------------------------------------------


async def get_memory(pool, *, memory_id: UUID, user_id: UUID) -> dict | None:
    row = await pool.fetchrow(
        "SELECT id, user_id, map_id, memory_type, content, is_active, created_at "
        "FROM public.agent_memories WHERE id = $1 AND user_id = $2",
        memory_id,
        user_id,
    )
    return _row_to_item(dict(row)) if row else None


# ---------------------------------------------------------------------------
# PATCH /memories/{id}
# ---------------------------------------------------------------------------


async def patch_memory(pool, *, memory_id: UUID, user_id: UUID, payload: MemoryPatchRequest) -> dict | None:
    """Update content and/or is_active. Returns updated row or None if not found."""
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

    if payload.memory_type is not None:
        sets.append(f"memory_type = ${idx}")
        args.append(payload.memory_type.strip())
        idx += 1

    if not sets:
        # Nothing to update — fetch and return existing
        return await get_memory(pool, memory_id=memory_id, user_id=user_id)

    # Append ownership filter args
    args.extend([memory_id, user_id])
    set_clause = ", ".join(sets)
    q = (
        f"UPDATE public.agent_memories SET {set_clause} "
        f"WHERE id = ${idx} AND user_id = ${idx + 1} "
        f"RETURNING id, user_id, map_id, memory_type, content, is_active, created_at"
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


async def propose_memory(pool, payload: MemoryProposeRequest) -> MemoryProposeResponse:
    """Validate, deduplicate, enforce per-user limit, and store a memory."""
    user_id = payload.user_id
    content = payload.content.strip()
    if not content:
        return MemoryProposeResponse(stored=False, reason="empty_content")

    # 0. If map_id is provided, verify map existence and ownership
    if payload.map_id is not None:
        map_owner = await pool.fetchval(
            "SELECT user_id FROM public.mind_maps WHERE id = $1",
            payload.map_id,
        )
        if map_owner is None:
            raise ValueError(f"Map {payload.map_id} not found.")
        if map_owner != user_id:
            raise PermissionError("User does not own the specified map.")

    # 1. Per-user count limit
    count: int = await pool.fetchval(
        "SELECT COUNT(*) FROM public.agent_memories WHERE user_id = $1",
        user_id,
    )
    if count >= MEMORY_PER_USER_LIMIT:
        logger.info("Memory limit reached for user %s (%d/%d)", user_id, count, MEMORY_PER_USER_LIMIT)
        return MemoryProposeResponse(stored=False, reason="limit_exceeded")

    # 2. Exact duplicate check (same user, same content lowercased)
    exact = await pool.fetchval(
        "SELECT id FROM public.agent_memories "
        "WHERE user_id = $1 AND lower(content) = lower($2) AND is_active = true "
        "LIMIT 1",
        user_id,
        content,
    )
    if exact is not None:
        return MemoryProposeResponse(stored=False, reason="duplicate", id=exact)

    # 3. Near-duplicate check (Jaccard on recent 100 active memories)
    rows = await pool.fetch(
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
    new_row = await pool.fetchrow(
        "INSERT INTO public.agent_memories (user_id, map_id, memory_type, content, is_active) "
        "VALUES ($1, $2, $3, $4, true) "
        "RETURNING id",
        user_id,
        payload.map_id,
        payload.memory_type,
        content,
    )
    return MemoryProposeResponse(stored=True, reason="created", id=new_row["id"])


# ---------------------------------------------------------------------------
# GDPR export — GET /memories/export
# ---------------------------------------------------------------------------


async def gdpr_export(pool, *, user_id: UUID) -> dict:
    rows = await pool.fetch(
        "SELECT id, map_id, memory_type, content, is_active, created_at "
        "FROM public.agent_memories WHERE user_id = $1 ORDER BY created_at DESC",
        user_id,
    )
    memories = [
        {
            "id": str(r["id"]),
            "map_id": str(r["map_id"]) if r["map_id"] else None,
            "memory_type": r["memory_type"],
            "content": r["content"],
            "is_active": r["is_active"],
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
