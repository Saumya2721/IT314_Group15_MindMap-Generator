from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg


async def create_db_pool(dsn: Optional[str] = None, min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    """Create asyncpg pool compatible with Supabase PostgreSQL."""
    db_dsn = dsn or os.getenv("DATABASE_URL")
    if not db_dsn:
        raise ValueError("DATABASE_URL environment variable is required")

    async def _init_connection(conn: asyncpg.Connection) -> None:
        for sch in ("extensions", "public"):
            try:
                await conn.set_type_codec(
                    "vector",
                    encoder=lambda v: "[" + ",".join(str(float(x)) for x in v) + "]",
                    decoder=lambda v: [float(x) for x in v.strip("[]").split(",")] if v else [],
                    schema=sch,
                    format="text",
                )
                break
            except Exception:
                continue

        await conn.set_type_codec(
            "jsonb",
            encoder=lambda v: json.dumps(v),
            decoder=lambda v: json.loads(v) if v else None,
            schema="pg_catalog",
            format="text",
        )

    return await asyncpg.create_pool(
        dsn=db_dsn,
        min_size=min_size,
        max_size=max_size,
        init=_init_connection,
        statement_cache_size=0,  # Supabase pooler compatibility
    )


@asynccontextmanager
async def transaction(pool: asyncpg.Pool):
    """Convenience context manager: `async with transaction(pool) as conn:`"""
    async with pool.acquire() as conn:
        async with conn.transaction():
            yield conn
