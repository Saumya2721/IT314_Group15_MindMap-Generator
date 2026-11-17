from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from db.connection import transaction
from db.repository import (
    assert_map_owner,
    check_and_bump_map_version,
    create_map_edge,
    create_map_node,
    create_map_snapshot,
    get_map_snapshot,
    update_map_edge,
    update_map_node,
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
        SELECT id, map_id, version_no AS version_number, trigger_job_id,
               target_node_id, reason, nodes_snapshot, edges_snapshot, graph, created_at
          FROM public.map_snapshots
         WHERE map_id = $1 AND version_no = $2
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
    """Atomically reconcile active nodes and edges on map with snapshot data non-destructively."""
    data: dict[str, Any] = json.loads(snapshot_data) if isinstance(snapshot_data, str) else dict(snapshot_data)

    nodes_raw = data.get("nodes", [])
    edges_raw = data.get("edges", [])

    # Non-destructive in-place reconciliation: preserve existing node IDs and their comments
    existing_node_rows = await conn.fetch("SELECT id FROM public.nodes WHERE map_id = $1", map_id)
    existing_node_ids = {r["id"] for r in existing_node_rows}
    snapshot_node_ids = {UUID(str(n["id"])) for n in nodes_raw if n.get("id")}

    nodes_to_delete = existing_node_ids - snapshot_node_ids
    if nodes_to_delete:
        await conn.execute(
            "DELETE FROM public.nodes WHERE map_id = $1 AND id = ANY($2::uuid[])",
            map_id,
            list(nodes_to_delete),
        )

    for n in nodes_raw:
        nid = UUID(str(n["id"]))
        src_job = UUID(str(n["source_job_id"])) if n.get("source_job_id") else None
        node_payload = {
            "label": n["label"],
            "description": n.get("description"),
            "pos_x": float(n.get("pos_x", 0.0)),
            "pos_y": float(n.get("pos_y", 0.0)),
            "background_color": n.get("background_color"),
            "text_color": n.get("text_color"),
            "width": float(n["width"]) if n.get("width") is not None else None,
            "height": float(n["height"]) if n.get("height") is not None else None,
            "node_type": n.get("node_type", "default"),
            "node_data": n.get("node_data"),
            "source_job_id": src_job,
        }
        if nid in existing_node_ids:
            await update_map_node(conn, map_id, nid, node_payload)
        else:
            await create_map_node(
                conn,
                node_id=nid,
                map_id=map_id,
                label=n["label"],
                description=n.get("description"),
                pos_x=float(n.get("pos_x", 0.0)),
                pos_y=float(n.get("pos_y", 0.0)),
                background_color=n.get("background_color"),
                text_color=n.get("text_color"),
                width=float(n["width"]) if n.get("width") is not None else None,
                height=float(n["height"]) if n.get("height") is not None else None,
                node_type=n.get("node_type", "default"),
                source_job_id=src_job,
                node_data=n.get("node_data"),
            )

    # Reconcile edges non-destructively
    existing_edge_rows = await conn.fetch("SELECT id FROM public.edges WHERE map_id = $1", map_id)
    existing_edge_ids = {r["id"] for r in existing_edge_rows}
    snapshot_edge_ids = {UUID(str(e["id"])) for e in edges_raw if e.get("id")}

    edges_to_delete = existing_edge_ids - snapshot_edge_ids
    if edges_to_delete:
        await conn.execute(
            "DELETE FROM public.edges WHERE map_id = $1 AND id = ANY($2::uuid[])",
            map_id,
            list(edges_to_delete),
        )

    for e in edges_raw:
        eid = UUID(str(e["id"])) if e.get("id") else None
        src_id = UUID(str(e.get("source_node_id") or e.get("source")))
        tgt_id = UUID(str(e.get("target_node_id") or e.get("target")))
        if eid and eid in existing_edge_ids:
            await update_map_edge(
                conn,
                map_id,
                eid,
                {
                    "source_node_id": src_id,
                    "target_node_id": tgt_id,
                    "label": e.get("label"),
                    "edge_data": e.get("edge_data"),
                },
            )
        else:
            await create_map_edge(
                conn,
                edge_id=eid or uuid4(),
                map_id=map_id,
                source_node_id=src_id,
                target_node_id=tgt_id,
                label=e.get("label"),
                edge_data=e.get("edge_data"),
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

        raw_snap_data = snap.get("snapshot_data")
        if raw_snap_data is None:
            if isinstance(snap.get("graph"), dict):
                raw_snap_data = snap["graph"]
            else:
                raw_snap_data = {
                    "nodes": snap.get("nodes_snapshot") or [],
                    "edges": snap.get("edges_snapshot") or [],
                }

        restored_data = await restore_snapshot_data_to_map(conn, map_id, raw_snap_data)

        await create_map_snapshot(
            conn,
            map_id=map_id,
            reason=f"Restored from version {snap.get('version_number', identifier)}",
        )

        old_version = snap.get("version_number", 1)
        new_version = await check_and_bump_map_version(conn, map_id)
        target_snapshot_id = snap.get("id") or snapshot_uuid or identifier

        return {
            "status": "restored",
            "map_id": str(map_id),
            "restored_version": old_version,
            "new_version": new_version,
            "version": new_version,
            "nodes_restored": len(raw_snap_data.get("nodes") or []),
            "edges_restored": len(raw_snap_data.get("edges") or []),
            "snapshot_id": str(target_snapshot_id),
            "snapshot_data": restored_data,
        }
