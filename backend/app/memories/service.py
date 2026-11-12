from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg
from memories.schemas import MemoryCreateRequest, MemoryUpdateRequest
from memories.validation import (
    check_near_duplicate,
    compute_content_hash,
    validate_memory_payload,
)

from db.repository import assert_map_owner

logger = logging.getLogger(__name__)


async def create_memory(
    pool: asyncpg.Pool,
    user_id: UUID,
    payload: MemoryCreateRequest,
) -> dict[str, Any]:
    """Create a new memory after validation, permission checks, and deduplication."""
    # 1. Validate payload attributes
    is_valid, err = validate_memory_payload(
        content=payload.content,
        category=payload.category,
        scope=payload.scope,
        map_id=payload.map_id,
    )
    if not is_valid:
        raise ValueError(err or "Validation failed")

    # 2. Check map ownership if map-scoped
    if payload.scope == "map" and payload.map_id is not None:
        is_owner = await assert_map_owner(pool, payload.map_id, user_id)
        if not is_owner:
            raise PermissionError(f"User does not own target mind map {payload.map_id}")

    content_hash = compute_content_hash(payload.content)

    async with pool.acquire() as conn:
        # 3. Exact deduplication check
        existing = await conn.fetchrow(
            """
            SELECT id, content FROM public.memories
             WHERE user_id = $1
               AND content_hash = $2
               AND scope = $3
               AND ((map_id = $4) OR ($4 IS NULL AND map_id IS NULL))
               AND is_active = true
            """,
            user_id,
            content_hash,
            payload.scope,
            payload.map_id,
        )
        if existing:
            raise ValueError(f"Duplicate memory already exists with ID {existing['id']}")

        # 4. Near-duplicate similarity check
        active_rows = await conn.fetch(
            """
            SELECT content FROM public.memories
             WHERE user_id = $1
               AND scope = $2
               AND ((map_id = $3) OR ($3 IS NULL AND map_id IS NULL))
               AND is_active = true
            """,
            user_id,
            payload.scope,
            payload.map_id,
        )
        existing_texts = [r["content"] for r in active_rows]
        near_dup = check_near_duplicate(payload.content, existing_texts)
        if near_dup:
            raise ValueError(f"Near-duplicate memory detected matching existing content: '{near_dup}'")

        # 5. Insert memory
        meta_json = json.dumps(payload.metadata) if payload.metadata else "{}"
        row = await conn.fetchrow(
            """
            INSERT INTO public.memories (
                user_id, map_id, content, category, scope, is_active, content_hash, metadata
            ) VALUES ($1, $2, $3, $4, $5, true, $6, $7::jsonb)
            RETURNING id, user_id, map_id, content, category, scope, is_active,
                      content_hash, metadata, created_at, updated_at
            """,
            user_id,
            payload.map_id,
            payload.content.strip(),
            payload.category.lower(),
            payload.scope,
            content_hash,
            meta_json,
        )

        logger.info("Created memory id=%s scope=%s for user=%s", row["id"], payload.scope, user_id)
        res = dict(row)
        if isinstance(res.get("metadata"), str):
            res["metadata"] = json.loads(res["metadata"])
        return res


async def list_memories(
    pool: asyncpg.Pool,
    user_id: UUID,
    scope: str = "all",
    map_id: UUID | None = None,
    is_active: bool | None = None,
    category: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """List memories for user with filtering, pagination, and total count."""
    conditions = ["user_id = $1"]
    args: list[Any] = [user_id]
    idx = 2

    if scope == "global":
        conditions.append("scope = 'global'")
    elif scope == "map":
        conditions.append("scope = 'map'")
        if map_id:
            conditions.append(f"map_id = ${idx}")
            args.append(map_id)
            idx += 1
    elif scope == "all" and map_id:
        # Composite view: global memories + target map memories
        conditions.append(f"(scope = 'global' OR (scope = 'map' AND map_id = ${idx}))")
        args.append(map_id)
        idx += 1

    if is_active is not None:
        conditions.append(f"is_active = ${idx}")
        args.append(is_active)
        idx += 1

    if category:
        conditions.append(f"category = ${idx}")
        args.append(category.lower())
        idx += 1

    if search:
        conditions.append(f"content ILIKE '%' || ${idx} || '%'")
        args.append(search.strip())
        idx += 1

    where_clause = " AND ".join(conditions)

    async with pool.acquire() as conn:
        count_query = f"SELECT count(*) FROM public.memories WHERE {where_clause}"
        total: int = await conn.fetchval(count_query, *args) or 0

        data_query = f"""
            SELECT id, user_id, map_id, content, category, scope, is_active,
                   content_hash, metadata, created_at, updated_at
              FROM public.memories
             WHERE {where_clause}
             ORDER BY updated_at DESC
             LIMIT ${idx} OFFSET ${idx + 1}
        """
        rows = await conn.fetch(data_query, *args, limit, offset)

        items: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("metadata"), str):
                d["metadata"] = json.loads(d["metadata"])
            items.append(d)

        return items, total


async def get_memory_by_id(
    pool: asyncpg.Pool,
    memory_id: UUID,
    user_id: UUID,
) -> dict[str, Any] | None:
    """Retrieve a single memory by ID belonging to user."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, user_id, map_id, content, category, scope, is_active,
                   content_hash, metadata, created_at, updated_at
              FROM public.memories
             WHERE id = $1 AND user_id = $2
            """,
            memory_id,
            user_id,
        )
        if not row:
            return None
        res = dict(row)
        if isinstance(res.get("metadata"), str):
            res["metadata"] = json.loads(res["metadata"])
        return res


async def update_memory(
    pool: asyncpg.Pool,
    memory_id: UUID,
    user_id: UUID,
    payload: MemoryUpdateRequest,
) -> dict[str, Any] | None:
    """Update memory content, category, or metadata."""
    existing = await get_memory_by_id(pool, memory_id, user_id)
    if not existing:
        return None

    new_content = payload.content.strip() if payload.content else existing["content"]
    new_category = payload.category.lower() if payload.category else existing["category"]

    is_valid, err = validate_memory_payload(
        content=new_content,
        category=new_category,
        scope=existing["scope"],
        map_id=existing["map_id"],
    )
    if not is_valid:
        raise ValueError(err or "Validation failed")

    new_hash = compute_content_hash(new_content)
    new_meta = payload.metadata if payload.metadata is not None else existing.get("metadata", {})
    meta_json = json.dumps(new_meta) if new_meta else "{}"

    async with pool.acquire() as conn:
        if new_hash != existing["content_hash"]:
            dup = await conn.fetchrow(
                """
                SELECT id FROM public.memories
                 WHERE user_id = $1 AND content_hash = $2 AND scope = $3
                   AND ((map_id = $4) OR ($4 IS NULL AND map_id IS NULL))
                   AND id != $5 AND is_active = true
                """,
                user_id,
                new_hash,
                existing["scope"],
                existing["map_id"],
                memory_id,
            )
            if dup:
                raise ValueError(f"Updated content duplicates existing memory {dup['id']}")

            other_active_rows = await conn.fetch(
                """
                SELECT content FROM public.memories
                 WHERE user_id = $1 AND scope = $2
                   AND ((map_id = $3) OR ($3 IS NULL AND map_id IS NULL))
                   AND id != $4 AND is_active = true
                """,
                user_id,
                existing["scope"],
                existing["map_id"],
                memory_id,
            )
            other_texts = [r["content"] for r in other_active_rows]
            near_dup = check_near_duplicate(new_content, other_texts)
            if near_dup:
                raise ValueError(f"Updated content near-duplicates existing memory: '{near_dup}'")

        row = await conn.fetchrow(
            """
            UPDATE public.memories
               SET content = $1,
                   category = $2,
                   content_hash = $3,
                   metadata = $4::jsonb,
                   updated_at = now()
             WHERE id = $5 AND user_id = $6
            RETURNING id, user_id, map_id, content, category, scope, is_active,
                      content_hash, metadata, created_at, updated_at
            """,
            new_content,
            new_category,
            new_hash,
            meta_json,
            memory_id,
            user_id,
        )
        if not row:
            return None
        res = dict(row)
        if isinstance(res.get("metadata"), str):
            res["metadata"] = json.loads(res["metadata"])
        return res


async def set_memory_active_status(
    pool: asyncpg.Pool,
    memory_id: UUID,
    user_id: UUID,
    is_active: bool,
) -> dict[str, Any] | None:
    """Enable or disable an existing memory."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE public.memories
               SET is_active = $1,
                   updated_at = now()
             WHERE id = $2 AND user_id = $3
            RETURNING id, user_id, map_id, content, category, scope, is_active,
                      content_hash, metadata, created_at, updated_at
            """,
            is_active,
            memory_id,
            user_id,
        )
        if not row:
            return None
        res = dict(row)
        if isinstance(res.get("metadata"), str):
            res["metadata"] = json.loads(res["metadata"])
        return res


async def delete_memory(
    pool: asyncpg.Pool,
    memory_id: UUID,
    user_id: UUID,
) -> bool:
    """Permanently delete a memory record."""
    async with pool.acquire() as conn:
        res = await conn.execute(
            "DELETE FROM public.memories WHERE id = $1 AND user_id = $2",
            memory_id,
            user_id,
        )
        # res looks like 'DELETE 1' or 'DELETE 0'
        return res.endswith("1")


async def gdpr_export_user_memories(
    pool: asyncpg.Pool,
    user_id: UUID,
) -> dict[str, Any]:
    """Export all memory records and metadata for a user under GDPR Article 15/20."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, user_id, map_id, content, category, scope, is_active,
                   content_hash, metadata, created_at, updated_at
              FROM public.memories
             WHERE user_id = $1
             ORDER BY created_at ASC
            """,
            user_id,
        )

        memories: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("metadata"), str):
                d["metadata"] = json.loads(d["metadata"])
            memories.append(d)

        # Retrieve any chat thread summaries linked to user's maps
        thread_rows = await conn.fetch(
            """
            SELECT t.id AS thread_id, t.map_id, t.summary, t.created_at
              FROM public.chat_threads t
              JOIN public.mind_maps m ON m.id = t.map_id
             WHERE m.user_id = $1 AND t.summary IS NOT NULL
            """,
            user_id,
        )
        summaries = [dict(t) for t in thread_rows]

        return {
            "user_id": str(user_id),
            "export_timestamp": datetime.now(UTC),
            "compliance_statement": (
                "GDPR Article 15 (Right of Access) and Article 20 (Data Portability) Export. "
                "Contains all user long-term preferences, persistent facts, and conversational summaries."
            ),
            "total_memories": len(memories),
            "memories": memories,
            "associated_map_summaries": summaries,
        }


async def gdpr_delete_user_memories(
    pool: asyncpg.Pool,
    user_id: UUID,
) -> dict[str, Any]:
    """Execute GDPR Article 17 (Right to Erasure / Right to be Forgotten) memory purge hook."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM public.memories WHERE user_id = $1",
            user_id,
        )
        deleted_count = 0
        try:
            deleted_count = int(result.split()[-1])
        except (ValueError, IndexError):
            deleted_count = 0

        logger.info("GDPR memory purge executed for user_id=%s, deleted_count=%d", user_id, deleted_count)
        return {
            "user_id": str(user_id),
            "deleted_count": deleted_count,
            "status": "purged",
            "confirmation_timestamp": datetime.now(UTC),
        }
