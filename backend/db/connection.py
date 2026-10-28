"""asyncpg connection pool for the Supabase PostgreSQL database.

The backend connects with DATABASE_URL as the `postgres` role, which owns the
tables and therefore bypasses Row Level Security. Every route checks map
ownership itself (repository.assert_map_owner) before touching a map.

Works with all three Supabase connection strings (Dashboard -> Connect):
direct connection, Session pooler, and Transaction pooler. The Transaction
pooler (port 6543) does not support named prepared statements, which is
why statement_cache_size is 0.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

logger = logging.getLogger(__name__)


def _encode_vector(v: list[float]) -> str:
    return "[" + ",".join(str(float(x)) for x in v) + "]"


def _decode_vector(v: str) -> list[float]:
    v = v.strip("[]")
    return [float(x) for x in v.split(",")] if v else []


async def _init_connection(conn: asyncpg.Connection) -> None:
    # json / jsonb <-> Python dict/list (nodes.node_data, edges.edge_data).
    for typename in ("jsonb", "json"):
        await conn.set_type_codec(
            typename,
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
            format="text",
        )

    # Legacy pgvector column (public.embeddings.embedding). Vectors now live
    # in Pinecone and the backend never reads or writes it; the codec is only
    # registered when the extension is still installed.
    vector_schema = await conn.fetchval(
        "SELECT n.nspname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
        "WHERE t.typname = 'vector' AND n.nspname IN ('extensions', 'public') LIMIT 1"
    )
    if vector_schema:
        try:
            await conn.set_type_codec(
                "vector",
                encoder=_encode_vector,
                decoder=_decode_vector,
                schema=vector_schema,
                format="text",
            )
        except Exception as exc:  # pragma: no cover - depends on the server
            logger.debug("pgvector codec not registered: %s", exc)


async def create_db_pool(
    dsn: str | None = None, min_size: int = 1, max_size: int = 10
) -> asyncpg.Pool:
    """Create the asyncpg pool. Raises ValueError when DATABASE_URL is missing."""
    db_dsn = (dsn or os.getenv("DATABASE_URL") or "").strip()
    if not db_dsn:
        raise ValueError("DATABASE_URL environment variable is required")

    return await asyncpg.create_pool(
        dsn=db_dsn,
        min_size=min_size,
        max_size=max_size,
        init=_init_connection,
        statement_cache_size=0,  # Supabase Transaction pooler compatibility
        command_timeout=60,
    )


@asynccontextmanager
async def transaction(pool: asyncpg.Pool) -> AsyncIterator[asyncpg.Connection]:
    """`async with transaction(pool) as conn:` runs everything in one transaction."""
    async with pool.acquire() as conn, conn.transaction():
        yield conn
