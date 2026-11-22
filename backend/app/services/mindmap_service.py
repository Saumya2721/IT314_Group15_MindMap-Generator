"""
Backend mind map service.

Owns all database transactions, job lifecycles, chat persistence, and calls the
decoupled LLM generator to produce and persist the graph.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from collections import deque
from typing import Any
from uuid import UUID, uuid4

import asyncpg
from contracts.models import DetailLevel, GenerationRequest
from contracts.models import JobType as ContractJobType
from core.config import settings
from fastapi import HTTPException
from maps.graph_validator import get_node_descendant_ids
from services.generator import MindMapGenerator
from services.queue import enqueue_generation_job

from db.connection import transaction
from db.models import (
    EdgeRecord,
    JobStatus,
    JobType,
    NodeRecord,
    SeedAssetType,
)
from db.repository import (
    OptimisticConcurrencyError,
    assert_map_owner,
    check_and_bump_map_version,
    create_generation_job,
    create_map_edge,
    create_map_node,
    create_map_snapshot,
    delete_map_edge,
    delete_map_node,
    get_node_by_id,
    get_or_create_chat_thread,
    has_active_job,
    has_active_node_job,
    insert_chat_message,
    insert_seed_asset,
    list_map_edges,
    list_map_nodes,
    lock_map_for_mutation,
    persist_generated_graph,
    replace_map_graph,
    update_job_status,
    update_map_edge,
    update_map_node,
)

logger = logging.getLogger("backend.services.mindmap_service")


class MindMapBackendService:
    def __init__(self, pool: asyncpg.Pool, generator: MindMapGenerator, redis_pool: Any = None) -> None:
        self.pool = pool
        self.generator = generator
        self.redis_pool = redis_pool

    async def generate(
        self,
        *,
        map_id: UUID,
        user_id: UUID,
        topic: str | None = None,
        ideas: str | None = None,
        detail_level: str | None = "overview",
        context: str | None = None,
        existing_job_id: UUID | None = None,
    ) -> dict[str, Any]:
        """
        Orchestrates DB lifecycle + LLM generation:

        1. DB: Inserts user turn into chat_messages
        2. DB: Creates generation_jobs row (status='running')
        3. LLM: Passes user inputs to MindMapGenerator
        4. DB: Persists generated nodes & edges into public.nodes & public.edges
        5. DB: Updates generation_jobs row to 'completed'
        6. Returns complete graph for immediate screen display / frontend.
        """
        job_id: UUID = existing_job_id or uuid4()
        summary_msg = f"Generate mind map for topic: '{topic or 'General'}' (Detail: {detail_level or 'overview'})"

        # Step 1: Initialize job & chat in DB
        thread_id: UUID
        async with transaction(self.pool) as conn:
            thread_id = await get_or_create_chat_thread(conn, map_id, user_id)
            msg_id = await insert_chat_message(conn, thread_id, "user", summary_msg)

            if existing_job_id:
                job_id = existing_job_id
                await conn.execute(
                    "UPDATE public.generation_jobs SET triggered_by_message_id = $2 WHERE id = $1",
                    job_id,
                    msg_id,
                )
            else:
                job = await create_generation_job(
                    conn,
                    map_id=map_id,
                    job_type=JobType.GENERATE,
                    triggered_by_message_id=msg_id,
                )
                job_id = job.id

            # Keep the original seed so regenerate_full() can rebuild the map later.
            seed_text = "\n\n".join(p.strip() for p in (topic, ideas, context) if p and p.strip())
            if seed_text:
                await insert_seed_asset(
                    conn,
                    generation_job_id=job_id,
                    asset_type=SeedAssetType.TEXT,
                    file_name=None,
                    storage_key=None,
                    mime_type=None,
                    content_text=seed_text,
                    file_size_bytes=len(seed_text.encode("utf-8")),
                    checksum_sha256=None,
                )

            await update_job_status(conn, job_id=job_id, status=JobStatus.RUNNING, progress_percent=20)

        # Step 2: Call LLM generator (decoupled, zero DB overhead)
        try:
            logger.info("Calling LLM generator for map %s, job %s...", map_id, job_id)
            graph_draft: dict[str, Any] = await self.generator.generate(
                topic=topic,
                ideas=ideas,
                detail_level=detail_level,
                context=context,
            )
        except Exception as exc:  # pragma: no cover - bubble up after DB update
            logger.exception("LLM generation failed for map %s: %s", map_id, exc)
            async with self.pool.acquire() as conn:
                await update_job_status(
                    conn,
                    job_id=job_id,
                    status=JobStatus.FAILED,
                    error_message=str(exc)[:500],
                )
            raise

        # Step 3: Map provisional string IDs to UUIDs and prepare DB records
        nodes_raw: list[dict[str, Any]] = graph_draft.get("nodes", [])
        edges_raw: list[dict[str, Any]] = graph_draft.get("edges", [])

        id_map: dict[str, UUID] = {str(n["id"]): uuid4() for n in nodes_raw}

        node_records: list[NodeRecord] = []
        for n in nodes_raw:
            original_id = str(n["id"])
            node_id = id_map[original_id]
            node_records.append(
                NodeRecord(
                    id=node_id,
                    map_id=map_id,
                    label=n["label"],
                    description=n.get("description"),
                    pos_x=float(n.get("pos_x", 0.0)),
                    pos_y=float(n.get("pos_y", 0.0)),
                    background_color=n.get("background_color"),
                    text_color=n.get("text_color"),
                    node_type=n.get("node_type", "default"),
                    source_job_id=job_id,
                    node_data={"original_id": original_id},
                )
            )

        edge_records: list[EdgeRecord] = []
        for e in edges_raw:
            src = str(e.get("source"))
            tgt = str(e.get("target"))
            source_uuid = id_map.get(src)
            target_uuid = id_map.get(tgt)
            if source_uuid and target_uuid:
                edge_records.append(
                    EdgeRecord(
                        id=uuid4(),
                        map_id=map_id,
                        source_node_id=source_uuid,
                        target_node_id=target_uuid,
                        label=e.get("label"),
                        edge_data=None,
                    )
                )

        # Step 4: Persist generated nodes & edges to backend DB
        async with transaction(self.pool) as conn:
            await lock_map_for_mutation(conn, map_id)
            await persist_generated_graph(
                conn,
                map_id=map_id,
                source_job_id=job_id,
                nodes=node_records,
                edges=edge_records,
            )
            await check_and_bump_map_version(conn, map_id)
            await update_job_status(
                conn,
                job_id=job_id,
                status=JobStatus.COMPLETED,
                progress_percent=100,
            )
            await insert_chat_message(
                conn,
                thread_id,
                "assistant",
                (f"Successfully generated mind map with {len(node_records)} nodes and {len(edge_records)} edges."),
            )

        logger.info("Successfully persisted graph for map %s (job %s)", map_id, job_id)

        # Step 5: Format response for frontend display
        formatted_nodes: list[dict[str, Any]] = [
            {
                "id": str(n.id),
                "original_id": n.node_data.get("original_id") if n.node_data else None,
                "label": n.label,
                "description": n.description,
                "pos_x": n.pos_x,
                "pos_y": n.pos_y,
                "background_color": n.background_color,
                "text_color": n.text_color,
                "node_type": n.node_type,
            }
            for n in node_records
        ]

        formatted_edges: list[dict[str, Any]] = [
            {
                "id": str(e.id),
                "source": str(e.source_node_id),
                "target": str(e.target_node_id),
                "label": e.label,
            }
            for e in edge_records
        ]

        return {
            "map_id": str(map_id),
            "job_id": str(job_id),
            "status": "completed",
            "nodes": formatted_nodes,
            "edges": formatted_edges,
        }

    async def regenerate_full(
        self,
        *,
        map_id: UUID,
        user_id: UUID,
        instruction: str,
        detail_level: DetailLevel | None = "balanced",
        existing_job_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Regenerate an existing map with a new instruction, replacing nodes & edges."""
        job_id: UUID = existing_job_id or uuid4()
        summary_msg = f"Regenerate mind map with instruction: '{instruction}'"
        callback_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(callback_token.encode("utf-8")).hexdigest()

        thread_id: UUID
        async with transaction(self.pool) as conn:
            thread_id = await get_or_create_chat_thread(conn, map_id, user_id)
            msg_id = await insert_chat_message(conn, thread_id, "user", summary_msg)
            if existing_job_id:
                job_id = existing_job_id
                await conn.execute(
                    "UPDATE public.generation_jobs SET triggered_by_message_id = $2, callback_token_hash = $3 WHERE id = $1",
                    job_id,
                    msg_id,
                    token_hash,
                )
            else:
                job = await create_generation_job(
                    conn,
                    map_id=map_id,
                    job_type=JobType.REGENERATE_FULL,
                    job_id=job_id,
                    triggered_by_message_id=msg_id,
                    callback_token_hash=token_hash,
                )
                job_id = job.id

            # U3/R8: Atomic pre-regeneration safety snapshot in the same transaction
            await create_map_snapshot(
                conn,
                map_id=map_id,
                trigger_job_id=job_id,
                reason=f"Pre-regeneration snapshot before full map regeneration ({job_id})",
            )

        # Route through Redis queue worker pipeline if Redis is available
        if self.redis_pool:
            has_file_seed = await self.pool.fetchval(
                """
                SELECT 1 FROM public.seed_assets a
                JOIN public.generation_jobs j ON j.id = a.generation_job_id
                WHERE j.map_id = $1 AND a.asset_type IN ('pdf', 'image')
                LIMIT 1
                """,
                map_id,
            )
            gen_req = GenerationRequest(
                job_id=job_id,
                map_id=map_id,
                job_type=ContractJobType.REGENERATE_FULL,
                regeneration_instruction=instruction,
                seed_has_files=bool(has_file_seed),
                detail_level=detail_level or "balanced",
                callback_base_url=settings.BACKEND_INTERNAL_URL,
                callback_token=callback_token,
            )
            try:
                enqueued_id = await enqueue_generation_job(self.redis_pool, gen_req.model_dump(mode="json"))
                if not enqueued_id:
                    raise RuntimeError("Redis declined job enqueue (returned None)")
            except Exception as exc:
                logger.exception("Failed to enqueue regenerate_full job %s: %s", job_id, exc)
                # U1/R1: Compensation updates status to rejected without requiring started_at IS NULL
                await self.pool.execute(
                    """
                    UPDATE public.generation_jobs
                       SET status = 'rejected',
                           completed_at = now(),
                           progress_percent = 0,
                           error_message = $2
                     WHERE id = $1 AND status IN ('pending', 'running')
                    """,
                    job_id,
                    f"Queue dispatch failed: {exc}",
                )
                raise HTTPException(503, f"Generation queue service is unavailable: {exc}")

            return {
                "map_id": str(map_id),
                "job_id": str(job_id),
                "status": "enqueued",
                "poll": f"/api/maps/{map_id}/jobs/{job_id}",
            }

        # Fallback path (when Redis queue worker is not configured): run in-process with error handling
        try:
            async with transaction(self.pool) as conn:
                await update_job_status(conn, job_id=job_id, status=JobStatus.RUNNING, progress_percent=20)

            original_seed: str | None = await self.pool.fetchval(
                (
                    "SELECT a.content_text FROM public.seed_assets a "
                    "JOIN public.generation_jobs j ON j.id = a.generation_job_id "
                    "WHERE j.map_id = $1 AND a.content_text IS NOT NULL "
                    "ORDER BY a.uploaded_at ASC LIMIT 1"
                ),
                map_id,
            )

            # Call LLM generator
            graph_draft: dict[str, Any] = await self.generator.generate(
                topic=None if original_seed else "Mind Map",
                ideas=original_seed,
                detail_level=detail_level,
                regeneration_instruction=instruction,
            )

            nodes_raw = graph_draft.get("nodes", [])
            edges_raw = graph_draft.get("edges", [])
            id_map = {str(n["id"]): uuid4() for n in nodes_raw}

            node_records = [
                NodeRecord(
                    id=id_map[str(n["id"])],
                    map_id=map_id,
                    label=n["label"],
                    description=n.get("description"),
                    pos_x=float(n.get("pos_x", 0.0)),
                    pos_y=float(n.get("pos_y", 0.0)),
                    background_color=n.get("background_color"),
                    text_color=n.get("text_color"),
                    node_type=n.get("node_type", "default"),
                    source_job_id=job_id,
                    node_data={"original_id": str(n["id"])},
                )
                for n in nodes_raw
            ]

            edge_records = [
                EdgeRecord(
                    id=uuid4(),
                    map_id=map_id,
                    source_node_id=id_map[str(e["source"])],
                    target_node_id=id_map[str(e["target"])],
                    label=e.get("label"),
                )
                for e in edges_raw
                if str(e["source"]) in id_map and str(e["target"]) in id_map
            ]

            # Atomically replace graph in DB
            async with transaction(self.pool) as conn:
                await lock_map_for_mutation(conn, map_id)
                await replace_map_graph(
                    conn,
                    map_id=map_id,
                    source_job_id=job_id,
                    nodes=node_records,
                    edges=edge_records,
                )
                await update_job_status(
                    conn,
                    job_id=job_id,
                    status=JobStatus.COMPLETED,
                    progress_percent=100,
                )
                await insert_chat_message(
                    conn,
                    thread_id,
                    "assistant",
                    (f"Regeneration complete with {len(node_records)} nodes and {len(edge_records)} edges."),
                )

            return {
                "map_id": str(map_id),
                "job_id": str(job_id),
                "status": "completed",
                "nodes": [
                    {
                        "id": str(n.id),
                        "label": n.label,
                        "description": n.description,
                        "pos_x": n.pos_x,
                        "pos_y": n.pos_y,
                    }
                    for n in node_records
                ],
                "edges": [
                    {
                        "id": str(e.id),
                        "source": str(e.source_node_id),
                        "target": str(e.target_node_id),
                        "label": e.label,
                    }
                    for e in edge_records
                ],
            }
        except Exception as exc:
            logger.exception("Full regeneration failed for job %s: %s", job_id, exc)
            async with transaction(self.pool) as conn:
                await update_job_status(
                    conn,
                    job_id=job_id,
                    status=JobStatus.FAILED,
                    error_message=f"Regeneration failed: {exc}",
                )
                await insert_chat_message(
                    conn,
                    thread_id,
                    "assistant",
                    f"Map regeneration failed: {exc}",
                )
            raise

    async def regenerate_node(
        self,
        *,
        map_id: UUID,
        node_id: UUID,
        user_id: UUID,
        instruction: str | None = None,
        detail_level: DetailLevel | None = "balanced",
    ) -> dict[str, Any]:
        """Initiate isolated branch regeneration rooted at node_id, taking safety snapshot and enqueueing or executing."""
        is_owner = await assert_map_owner(self.pool, map_id, user_id)
        if not is_owner:
            raise HTTPException(404, "Map not found")

        target_node = await get_node_by_id(self.pool, map_id, node_id)
        if not target_node:
            raise HTTPException(404, "Node not found on this map")

        if await has_active_node_job(self.pool, map_id, node_id):
            raise HTTPException(409, "Regeneration already in progress for this node.")
        if await has_active_job(self.pool, map_id):
            raise HTTPException(409, "Generation already in progress for this map.")

        # F3: Check queue availability BEFORE taking snapshot or writing DB records
        if not self.redis_pool:
            raise HTTPException(503, "Generation queue service is unavailable. Redis connection is required.")

        resolved_instruction = (
            instruction or ""
        ).strip() or f"Refine and expand the mind map branch for node '{target_node['label']}'"

        edges_rows = await list_map_edges(self.pool, map_id)
        edges_data = [dict(e) for e in edges_rows]
        nodes_rows = await list_map_nodes(self.pool, map_id)
        nodes_map = {str(n["id"]): dict(n) for n in nodes_rows}

        descendant_ids = get_node_descendant_ids(edges_data, node_id)
        branch_node_ids = {str(node_id)} | {str(d) for d in descendant_ids}

        branch_nodes = [nodes_map[nid] for nid in branch_node_ids if nid in nodes_map]
        branch_edges = [
            e
            for e in edges_data
            if str(e.get("source_node_id") or e.get("source")) in branch_node_ids
            and str(e.get("target_node_id") or e.get("target")) in branch_node_ids
        ]

        # Multi-parent ancestor labels reverse BFS
        ancestor_labels: list[str] = []
        ancestor_queue = deque([str(node_id)])
        visited_ancestors = {str(node_id)}
        while ancestor_queue:
            curr = ancestor_queue.popleft()
            for e in edges_data:
                tgt = str(e.get("target_node_id") or e.get("target"))
                src = str(e.get("source_node_id") or e.get("source"))
                if tgt == curr and src and src not in visited_ancestors:
                    visited_ancestors.add(src)
                    ancestor_queue.append(src)
                    if src in nodes_map and nodes_map[src].get("label"):
                        ancestor_labels.append(nodes_map[src]["label"])
        ancestor_labels.reverse()

        # All incoming parent edges
        incoming_parent_edges = [
            {
                "source_node_id": str(e.get("source_node_id") or e.get("source")),
                "target_node_id": str(e.get("target_node_id") or e.get("target")),
                "label": e.get("label"),
            }
            for e in edges_data
            if str(e.get("target_node_id") or e.get("target")) == str(node_id)
        ]

        branch_context = {
            "target_node_id": str(node_id),
            "target_node_label": target_node["label"],
            "target_node_description": target_node.get("description"),
            "descendant_ids": [str(d) for d in descendant_ids],
            "nodes": branch_nodes,
            "branch_nodes": branch_nodes,
            "edges": branch_edges,
            "branch_edges": branch_edges,
            "ancestor_labels": ancestor_labels,
            "parent_edges": incoming_parent_edges,
            "parent_edge": incoming_parent_edges[0] if incoming_parent_edges else None,
        }

        job_id = uuid4()
        callback_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(callback_token.encode("utf-8")).hexdigest()

        summary_msg = f"Regenerate branch for node '{target_node['label']}': '{resolved_instruction}'"
        snapshot_id: UUID
        expected_map_version: int
        expected_node_version: int = int(target_node.get("version") if target_node.get("version") is not None else 1)
        async with transaction(self.pool) as conn:
            expected_map_version = await lock_map_for_mutation(conn, map_id)
            thread_id = await get_or_create_chat_thread(conn, map_id, user_id)
            msg_id = await insert_chat_message(conn, thread_id, "user", summary_msg)
            await create_generation_job(
                conn,
                map_id=map_id,
                job_type=JobType.REGENERATE_NODE,
                job_id=job_id,
                target_node_id=node_id,
                triggered_by_message_id=msg_id,
                callback_token_hash=token_hash,
            )
            # U4/R8: Create safety snapshot atomically within the exact same transaction
            snapshot_id = await create_map_snapshot(
                conn,
                map_id=map_id,
                target_node_id=node_id,
                trigger_job_id=job_id,
                reason=f"Pre-regeneration snapshot for node '{target_node['label']}' ({node_id})",
            )

        has_file_seed = await self.pool.fetchval(
            """
            SELECT 1 FROM public.seed_assets a
            JOIN public.generation_jobs j ON j.id = a.generation_job_id
            WHERE j.map_id = $1 AND a.asset_type IN ('pdf', 'image')
            LIMIT 1
            """,
            map_id,
        )
        gen_req = GenerationRequest(
            job_id=job_id,
            map_id=map_id,
            job_type=ContractJobType.REGENERATE_NODE,
            target_node_id=node_id,
            ancestor_labels=ancestor_labels,
            branch_context=branch_context,
            regeneration_instruction=resolved_instruction,
            seed_has_files=bool(has_file_seed),
            detail_level=detail_level,
            expected_map_version=expected_map_version,
            expected_node_version=expected_node_version,
            callback_base_url=settings.BACKEND_INTERNAL_URL,
            callback_token=callback_token,
        )

        # F3/U1: Compensate and mark job rejected if Redis enqueue fails
        try:
            enqueued_id = await enqueue_generation_job(self.redis_pool, gen_req.model_dump(mode="json"))
            if not enqueued_id:
                raise RuntimeError("Redis declined job enqueue (returned None)")
        except Exception as exc:
            logger.exception("Failed to enqueue regeneration job %s: %s", job_id, exc)
            try:
                await self.pool.execute(
                    """
                    UPDATE public.generation_jobs
                       SET status = 'rejected',
                           completed_at = now(),
                           progress_percent = 0,
                           error_message = $2
                     WHERE id = $1 AND status IN ('pending', 'running')
                    """,
                    job_id,
                    f"Failed to queue regeneration job: {exc}",
                )
            except Exception as db_exc:
                logger.warning("Failed to mark job %s rejected during compensation: %s", job_id, db_exc)
            raise HTTPException(status_code=502, detail=f"Failed to enqueue regeneration task: {exc}")

        return {
            "status": "accepted",
            "map_id": str(map_id),
            "job_id": str(job_id),
            "target_node_id": str(node_id),
            "snapshot_id": str(snapshot_id) if snapshot_id else None,
            "poll": f"/api/maps/{map_id}/jobs/{job_id}",
        }

    async def batch_update_graph(
        self,
        *,
        map_id: UUID,
        user_id: UUID,
        updates: dict[str, Any] | Any,
        expected_version: int | None = None,
        validate_graph: bool = True,
    ) -> dict[str, Any]:
        """Atomic multi-operation graph modification supporting optimistic concurrency and same-map edge validation."""
        is_owner = await assert_map_owner(self.pool, map_id, user_id)
        if not is_owner:
            raise HTTPException(404, "Map not found")

        # Extract payload components whether from dict or pydantic model
        if hasattr(updates, "model_dump"):
            body_dict = updates.model_dump(mode="json")
            exp_ver = getattr(updates, "expected_version", expected_version)
        elif isinstance(updates, dict):
            body_dict = updates
            exp_ver = updates.get("expected_version", expected_version)
        else:
            body_dict = dict(updates)
            exp_ver = expected_version

        nodes_list = body_dict.get("nodes")
        edges_list = body_dict.get("edges")
        create_nodes = body_dict.get("create_nodes") or []
        update_nodes = body_dict.get("update_nodes") or []
        delete_nodes = body_dict.get("delete_nodes") or []
        create_edges = body_dict.get("create_edges") or []
        update_edges = body_dict.get("update_edges") or []
        delete_edges = body_dict.get("delete_edges") or []

        # F2 & F9: Validate edge endpoints BEFORE version bump
        if nodes_list is not None and edges_list is not None:
            # Wholesale reconciliation branch:
            # All incoming nodes form the valid set of node IDs on this map
            incoming_node_ids = {
                UUID(str(n["id"])) if isinstance(n, dict) else UUID(str(n.id))
                for n in nodes_list
                if (isinstance(n, dict) and n.get("id")) or (hasattr(n, "id") and n.id)
            }
            if not incoming_node_ids:
                raise HTTPException(422, "Wholesale graph update requires at least one node; cannot wipe map to empty.")
            for e in edges_list:
                src_id = UUID(str(e.get("source_node_id") if isinstance(e, dict) else e.source_node_id))
                tgt_id = UUID(str(e.get("target_node_id") if isinstance(e, dict) else e.target_node_id))
                if src_id == tgt_id:
                    raise HTTPException(422, "Self-loops are not allowed")
                if src_id not in incoming_node_ids or tgt_id not in incoming_node_ids:
                    raise HTTPException(
                        422,
                        f"Edge endpoints ({src_id} -> {tgt_id}) must belong to this map.",
                    )
        async with transaction(self.pool) as conn:
            try:
                new_version = await check_and_bump_map_version(conn, map_id, expected_version=exp_ver)
            except OptimisticConcurrencyError as err:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "OptimisticLockConflict",
                        "message": (
                            f"Graph has been modified by another operation. "
                            f"Current version is {err.current_version}, but expected {err.expected_version}."
                        ),
                        "current_version": err.current_version,
                        "expected_version": err.expected_version,
                    },
                )

            # Apply wholesale update if nodes and edges provided
            if nodes_list is not None and edges_list is not None:
                existing_node_rows = await conn.fetch("SELECT id FROM public.nodes WHERE map_id = $1", map_id)
                existing_node_ids = {r["id"] for r in existing_node_rows}
                incoming_node_map = {
                    (UUID(str(n["id"])) if isinstance(n, dict) else UUID(str(n.id))): n
                    for n in nodes_list
                    if (isinstance(n, dict) and n.get("id")) or (hasattr(n, "id") and n.id)
                }

                nodes_to_delete = existing_node_ids - set(incoming_node_map.keys())
                if nodes_to_delete:
                    await conn.execute(
                        "DELETE FROM public.nodes WHERE map_id = $1 AND id = ANY($2::uuid[])",
                        map_id,
                        list(nodes_to_delete),
                    )

                for nid, n in incoming_node_map.items():
                    n_label = (n.get("label") if isinstance(n, dict) else n.label) or ""
                    n_desc = n.get("description") if isinstance(n, dict) else getattr(n, "description", None)
                    n_pos_x = float(n.get("pos_x", 0.0) if isinstance(n, dict) else getattr(n, "pos_x", 0.0))
                    n_pos_y = float(n.get("pos_y", 0.0) if isinstance(n, dict) else getattr(n, "pos_y", 0.0))
                    n_bg = n.get("background_color") if isinstance(n, dict) else getattr(n, "background_color", None)
                    n_tc = n.get("text_color") if isinstance(n, dict) else getattr(n, "text_color", None)
                    n_w = (
                        float(n["width"])
                        if (isinstance(n, dict) and n.get("width") is not None)
                        else getattr(n, "width", None)
                    )
                    n_h = (
                        float(n["height"])
                        if (isinstance(n, dict) and n.get("height") is not None)
                        else getattr(n, "height", None)
                    )
                    n_type = (
                        n.get("node_type") if isinstance(n, dict) else getattr(n, "node_type", "default")
                    ) or "default"
                    n_data = n.get("node_data") if isinstance(n, dict) else getattr(n, "node_data", None)

                    if nid in existing_node_ids:
                        upd = {
                            "label": n_label.strip(),
                            "description": n_desc.strip() if n_desc else None,
                            "pos_x": n_pos_x,
                            "pos_y": n_pos_y,
                            "background_color": n_bg,
                            "text_color": n_tc,
                            "width": n_w,
                            "height": n_h,
                            "node_type": n_type,
                        }
                        if n_data is not None:
                            upd["node_data"] = n_data
                        await update_map_node(conn, map_id, nid, upd)
                    else:
                        await create_map_node(
                            conn,
                            node_id=nid,
                            map_id=map_id,
                            label=n_label.strip(),
                            description=n_desc.strip() if n_desc else None,
                            pos_x=n_pos_x,
                            pos_y=n_pos_y,
                            background_color=n_bg,
                            text_color=n_tc,
                            width=n_w,
                            height=n_h,
                            node_type=n_type,
                            node_data=n_data,
                        )

                # Reconcile edges non-destructively
                existing_edge_rows = await conn.fetch("SELECT id FROM public.edges WHERE map_id = $1", map_id)
                existing_edge_ids = {r["id"] for r in existing_edge_rows if r.get("id") is not None}
                incoming_edge_map = {}
                for e in edges_list:
                    eid = (
                        UUID(str(e.get("id")))
                        if (isinstance(e, dict) and e.get("id"))
                        else (getattr(e, "id", None) or uuid4())
                    )
                    incoming_edge_map[eid] = e

                edges_to_delete = existing_edge_ids - set(incoming_edge_map.keys())
                if edges_to_delete:
                    await conn.execute(
                        "DELETE FROM public.edges WHERE map_id = $1 AND id = ANY($2::uuid[])",
                        map_id,
                        list(edges_to_delete),
                    )

                for eid, e in incoming_edge_map.items():
                    src_id = UUID(str(e.get("source_node_id") if isinstance(e, dict) else e.source_node_id))
                    tgt_id = UUID(str(e.get("target_node_id") if isinstance(e, dict) else e.target_node_id))
                    lbl = e.get("label") if isinstance(e, dict) else getattr(e, "label", None)
                    edata = e.get("edge_data") if isinstance(e, dict) else getattr(e, "edge_data", None)

                    if eid in existing_edge_ids:
                        edge_upd = {
                            "source_node_id": src_id,
                            "target_node_id": tgt_id,
                            "label": lbl,
                        }
                        if edata is not None:
                            edge_upd["edge_data"] = edata
                        await update_map_edge(conn, map_id, eid, edge_upd)
                    else:
                        await create_map_edge(
                            conn,
                            edge_id=eid,
                            map_id=map_id,
                            source_node_id=src_id,
                            target_node_id=tgt_id,
                            label=lbl,
                            edge_data=edata,
                        )
            else:
                # Apply delta mutations
                for nid in delete_nodes:
                    await delete_map_node(conn, map_id, UUID(str(nid)))

                if isinstance(update_nodes, dict):
                    for nid, n_upd in update_nodes.items():
                        await update_map_node(conn, map_id, UUID(str(nid)), n_upd)
                elif isinstance(update_nodes, list):
                    for n_upd in update_nodes:
                        nid = UUID(str(n_upd.get("id") if isinstance(n_upd, dict) else n_upd.id))
                        payload = dict(n_upd) if isinstance(n_upd, dict) else n_upd.model_dump(mode="json")
                        upd_data = {k: v for k, v in payload.items() if k != "id" and v is not None}
                        await update_map_node(conn, map_id, nid, upd_data)

                created_node_alias_map: dict[str, UUID] = {}

                for n_crt in create_nodes:
                    lbl = (n_crt.get("label") if isinstance(n_crt, dict) else getattr(n_crt, "label", "")) or ""
                    raw_id = n_crt.get("id") if isinstance(n_crt, dict) else getattr(n_crt, "id", None)
                    resolved_node_id: UUID
                    if raw_id is not None:
                        try:
                            resolved_node_id = UUID(str(raw_id))
                        except (ValueError, TypeError):
                            resolved_node_id = uuid4()
                        created_node_alias_map[str(raw_id)] = resolved_node_id
                    else:
                        resolved_node_id = uuid4()

                    await create_map_node(
                        conn,
                        node_id=resolved_node_id,
                        map_id=map_id,
                        label=str(lbl),
                        description=n_crt.get("description")
                        if isinstance(n_crt, dict)
                        else getattr(n_crt, "description", None),
                        pos_x=float(
                            n_crt.get("pos_x", 0.0) if isinstance(n_crt, dict) else getattr(n_crt, "pos_x", 0.0)
                        ),
                        pos_y=float(
                            n_crt.get("pos_y", 0.0) if isinstance(n_crt, dict) else getattr(n_crt, "pos_y", 0.0)
                        ),
                    )

                for eid in delete_edges:
                    await delete_map_edge(conn, map_id, UUID(str(eid)))

                if isinstance(update_edges, dict):
                    for eid, e_upd in update_edges.items():
                        await update_map_edge(conn, map_id, UUID(str(eid)), e_upd)
                elif isinstance(update_edges, list):
                    for e_upd in update_edges:
                        eid = UUID(str(e_upd.get("id") if isinstance(e_upd, dict) else e_upd.id))
                        payload = dict(e_upd) if isinstance(e_upd, dict) else e_upd.model_dump(mode="json")
                        upd_data = {k: v for k, v in payload.items() if k != "id" and v is not None}
                        await update_map_edge(conn, map_id, eid, upd_data)

                if create_edges:
                    # R2/L8: Pre-fetch valid node IDs on this map (including newly created nodes) to validate edge endpoints
                    valid_node_ids = {UUID(str(r["id"])) for r in await list_map_nodes(conn, map_id)} | set(
                        created_node_alias_map.values()
                    )

                    for e_crt in create_edges:
                        raw_edge_id = e_crt.get("id") if isinstance(e_crt, dict) else getattr(e_crt, "id", None)
                        resolved_edge_id: UUID
                        if raw_edge_id is not None and str(raw_edge_id).strip():
                            try:
                                resolved_edge_id = UUID(str(raw_edge_id).strip())
                            except (ValueError, TypeError):
                                raise HTTPException(
                                    status_code=422,
                                    detail=f"Invalid edge id '{raw_edge_id}' in create_edges. Must be a valid UUID.",
                                )
                        else:
                            resolved_edge_id = uuid4()

                        raw_src = (
                            e_crt.get("source_node_id")
                            if isinstance(e_crt, dict)
                            else getattr(e_crt, "source_node_id", None)
                        )
                        if raw_src is None:
                            raw_src = e_crt.get("source") if isinstance(e_crt, dict) else getattr(e_crt, "source", None)

                        raw_tgt = (
                            e_crt.get("target_node_id")
                            if isinstance(e_crt, dict)
                            else getattr(e_crt, "target_node_id", None)
                        )
                        if raw_tgt is None:
                            raw_tgt = e_crt.get("target") if isinstance(e_crt, dict) else getattr(e_crt, "target", None)

                        src_str = str(raw_src) if raw_src is not None else ""
                        tgt_str = str(raw_tgt) if raw_tgt is not None else ""

                        if src_str in created_node_alias_map:
                            resolved_src = created_node_alias_map[src_str]
                        else:
                            try:
                                resolved_src = UUID(src_str)
                            except (ValueError, TypeError):
                                raise HTTPException(
                                    status_code=422,
                                    detail=f"Invalid source_node_id '{raw_src}' in create_edges.",
                                )

                        if tgt_str in created_node_alias_map:
                            resolved_tgt = created_node_alias_map[tgt_str]
                        else:
                            try:
                                resolved_tgt = UUID(tgt_str)
                            except (ValueError, TypeError):
                                raise HTTPException(
                                    status_code=422,
                                    detail=f"Invalid target_node_id '{raw_tgt}' in create_edges.",
                                )

                        if resolved_src == resolved_tgt:
                            raise HTTPException(status_code=422, detail="Self-loops are not allowed")

                        # Verify that both endpoints actually belong to this map
                        if resolved_src not in valid_node_ids:
                            raise HTTPException(
                                status_code=422,
                                detail=f"Source node '{raw_src}' does not exist on this map.",
                            )
                        if resolved_tgt not in valid_node_ids:
                            raise HTTPException(
                                status_code=422,
                                detail=f"Target node '{raw_tgt}' does not exist on this map.",
                            )

                        await create_map_edge(
                            conn,
                            edge_id=resolved_edge_id,
                            map_id=map_id,
                            source_node_id=resolved_src,
                            target_node_id=resolved_tgt,
                            label=e_crt.get("label") if isinstance(e_crt, dict) else getattr(e_crt, "label", None),
                        )

            # F1: In-transaction check ensuring map is not left empty
            remaining_node_rows = await conn.fetch("SELECT * FROM public.nodes WHERE map_id = $1", map_id)
            if not remaining_node_rows:
                raise HTTPException(
                    status_code=422,
                    detail="Batch graph update rejected: applying it would leave the mind map with zero nodes.",
                )

            # F4: In-transaction DAG / cycle / connectivity validation
            if validate_graph:
                remaining_edge_rows = await conn.fetch("SELECT * FROM public.edges WHERE map_id = $1", map_id)
                from maps.graph_validator import validate_graph_structure

                nodes_data = [dict(r) for r in remaining_node_rows]
                edges_data = [dict(r) for r in remaining_edge_rows]
                val_errors = validate_graph_structure(nodes_data, edges_data, map_id=map_id)
                if val_errors:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "error": "GraphValidationError",
                            "message": "Graph topology validation failed.",
                            "errors": val_errors,
                        },
                    )

        updated_nodes = await list_map_nodes(self.pool, map_id)
        updated_edges = await list_map_edges(self.pool, map_id)
        return {
            "status": "success",
            "map_id": str(map_id),
            "version": new_version,
            "nodes": [dict(n) for n in updated_nodes],
            "edges": [dict(e) for e in updated_edges],
        }
