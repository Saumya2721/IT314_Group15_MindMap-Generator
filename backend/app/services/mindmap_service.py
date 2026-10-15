"""
Backend mind map service.

Owns all database transactions, job lifecycles, chat persistence, and calls the
decoupled LLM generator to produce and persist the graph.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

import asyncpg

from db.connection import transaction
from db.models import (
    EdgeRecord,
    JobStatus,
    JobType,
    NodeRecord,
    SeedAssetType,
)
from db.repository import (
    create_generation_job,
    get_or_create_chat_thread,
    insert_chat_message,
    insert_seed_asset,
    persist_generated_graph,
    replace_map_graph,
    update_job_status,
)
from services.generator import MindMapGenerator

logger = logging.getLogger("backend.services.mindmap_service")


class MindMapBackendService:
    def __init__(self, pool: asyncpg.Pool, generator: MindMapGenerator) -> None:
        self.pool = pool
        self.generator = generator

    async def generate(
        self,
        *,
        map_id: UUID,
        user_id: UUID,
        topic: Optional[str] = None,
        ideas: Optional[str] = None,
        detail_level: Optional[str] = "overview",
        context: Optional[str] = None,
        existing_job_id: Optional[UUID] = None,
    ) -> Dict[str, Any]:
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
        summary_msg = (
            f"Generate mind map for topic: '{topic or 'General'}' "
            f"(Detail: {detail_level or 'overview'})"
        )

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
            seed_text = "\n\n".join(
                p.strip() for p in (topic, ideas, context) if p and p.strip()
            )
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

            await update_job_status(
                conn, job_id=job_id, status=JobStatus.RUNNING, progress_percent=20
            )

        # Step 2: Call LLM generator (decoupled, zero DB overhead)
        try:
            logger.info(
                "Calling LLM generator for map %s, job %s...", map_id, job_id
            )
            graph_draft: Dict[str, Any] = await self.generator.generate(
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
        nodes_raw: List[Dict[str, Any]] = graph_draft.get("nodes", [])
        edges_raw: List[Dict[str, Any]] = graph_draft.get("edges", [])

        id_map: Dict[str, UUID] = {str(n["id"]): uuid4() for n in nodes_raw}

        node_records: List[NodeRecord] = []
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

        edge_records: List[EdgeRecord] = []
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
            await persist_generated_graph(
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
                (
                    f"Successfully generated mind map with {len(node_records)} nodes "
                    f"and {len(edge_records)} edges."
                ),
            )

        logger.info("Successfully persisted graph for map %s (job %s)", map_id, job_id)

        # Step 5: Format response for frontend display
        formatted_nodes: List[Dict[str, Any]] = [
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

        formatted_edges: List[Dict[str, Any]] = [
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
        detail_level: Optional[str] = "balanced",
        existing_job_id: Optional[UUID] = None,
    ) -> Dict[str, Any]:
        """Regenerate an existing map with a new instruction, replacing nodes & edges."""
        job_id: UUID = existing_job_id or uuid4()
        summary_msg = f"Regenerate mind map with instruction: '{instruction}'"

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
                    job_type=JobType.REGENERATE_FULL,
                    triggered_by_message_id=msg_id,
                )
                job_id = job.id

            await update_job_status(
                conn, job_id=job_id, status=JobStatus.RUNNING, progress_percent=20
            )

        # Rebuild the original seed saved by generate()
        original_seed: Optional[str] = await self.pool.fetchval(
            (
                "SELECT a.content_text FROM public.seed_assets a "
                "JOIN public.generation_jobs j ON j.id = a.generation_job_id "
                "WHERE j.map_id = $1 AND a.content_text IS NOT NULL "
                "ORDER BY a.uploaded_at ASC LIMIT 1"
            ),
            map_id,
        )

        # Call LLM
        graph_draft: Dict[str, Any] = await self.generator.generate(
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
                (
                    f"Regeneration complete with {len(node_records)} nodes "
                    f"and {len(edge_records)} edges."
                ),
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
                    "background_color": n.background_color,
                    "text_color": n.text_color,
                    "node_type": n.node_type,
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
