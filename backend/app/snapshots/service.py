from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg

from db.connection import transaction
from db.repository import (
    assert_map_owner,
    create_map_snapshot,
    get_map_snapshot,
)

logger = logging.getLogger(__name__)


async def get_snapshot_by_version(
    conn: asyncpg.Connection,
    map_id: UUID,
    version_number: int,
) -> dict[str, Any] | None:
    """Retrieve a snapshot record by map_id and version_number."""
    row = await conn.fetchrow(
        """
        SELECT id, map_id, version_number, trigger_source, trigger_job_id,
               label, snapshot_data, created_by, created_at
          FROM public.map_snapshots
         WHERE map_id = $1 AND version_number = $2
        """,
        map_id,
        version_number,
    )
    return dict(row) if row else None


async def restore_snapshot_data_to_map(
    conn: asyncpg.Connection,
    map_id: UUID,
    snapshot_data: dict[str, Any] | str,
) -> dict[str, Any]:
    """Atomically replace active nodes and edges on map with snapshot data."""
    data: dict[str, Any] = json.loads(snapshot_data) if isinstance(snapshot_data, str) else dict(snapshot_data)

    nodes_raw = data.get("nodes", [])
    edges_raw = data.get("edges", [])

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

    return data


async def restore_snapshot_by_version_or_id(
    pool: asyncpg.Pool,
    map_id: UUID,
    identifier: str,
    user_id: UUID,
) -> dict[str, Any] | None:
    """Restore nodes and edges from a snapshot by either version_number (int) or snapshot UUID.

    Creates a new snapshot entry with incremented version number tracking the restore.
    """
    is_owner = await assert_map_owner(pool, map_id, user_id)
    if not is_owner:
        return None

    version_no: int | None = None
    snapshot_uuid: UUID | None = None
    try:
        version_no = int(identifier)
    except ValueError:
        try:
            snapshot_uuid = UUID(identifier)
        except ValueError:
            return None

    async with transaction(pool) as conn:
        snap: dict[str, Any] | None = None
        if version_no is not None:
            snap = await get_snapshot_by_version(conn, map_id, version_no)
        elif snapshot_uuid is not None:
            snap = await get_map_snapshot(conn, map_id, snapshot_uuid)

        if not snap:
            return None

        restored_data = await restore_snapshot_data_to_map(conn, map_id, snap["snapshot_data"])

        snap_row = await create_map_snapshot(
            conn,
            map_id=map_id,
            trigger_source="manual",
            trigger_job_id=None,
            label=f"Restored from version {snap['version_number']}",
            snapshot_data=restored_data,
            created_by=user_id,
        )

        new_version = snap_row["version_number"] if snap_row else snap["version_number"] + 1
        new_snap_id = snap_row["id"] if snap_row else snap["id"]

        return {
            "status": "restored",
            "map_id": str(map_id),
            "restored_version": snap["version_number"],
            "new_version": new_version,
            "snapshot_id": str(new_snap_id),
            "snapshot_data": restored_data,
        }
