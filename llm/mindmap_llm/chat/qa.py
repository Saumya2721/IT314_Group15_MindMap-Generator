"""
Map-grounded question answering and small-talk handling.

Question answering pipeline:
  1. Retrieve top-k chunks from Pinecone (namespace = str(map_id)).
  2. Rerank with the decision model (same path as the generation pipeline).
  3. Build a grounded prompt using map nodes/edges + retrieved chunks.
  4. Call LLM once — single generation turn, no retry loop needed.
  5. Post-process to extract cited node IDs and source references.

Small talk:
  - Single LLM call with a minimal prompt.
  - No Pinecone retrieval — avoids wasting quota on "Hello!".
  - No action proposal returned.
"""

from __future__ import annotations

import logging
import re
from uuid import UUID

from chat.prompts import (
    build_chat_qa_system_prompt,
    build_chat_qa_user_prompt,
    build_small_talk_system_prompt,
    build_small_talk_user_prompt,
)
from contracts.models import ChatIntent, ChatTurnRequest, ChatTurnResponse
from orchestration.deps import GenerationDeps
from providers.base import Message
from rag.retrieval import rerank_context, retrieve_context

logger = logging.getLogger(__name__)

_QA_TOP_K_RETRIEVE = 10
_QA_TOP_K_RERANK = 5
_QA_MAX_TOKENS = 1024

# Pattern to extract [node:<uuid>] citations from LLM output.
_NODE_CITE_RE = re.compile(r"\[node:([0-9a-fA-F-]{36})\]")
# Pattern to extract [<number>] source citations.
_SOURCE_CITE_RE = re.compile(r"\[(\d+)\]")


async def answer_question(
    *,
    deps: GenerationDeps,
    request: ChatTurnRequest,
) -> ChatTurnResponse:
    """Return a grounded answer for a 'question' intent turn."""
    namespace = str(request.map_id)

    # Stage 1 — retrieve
    try:
        raw_chunks = await retrieve_context(
            vector_store=deps.vector_store,
            namespace=namespace,
            query=request.message,
            embedding_provider=deps.embedding_provider,
            top_k=_QA_TOP_K_RETRIEVE,
        )
    except Exception:
        logger.exception("Pinecone retrieval failed during QA; proceeding with map-only context")
        raw_chunks = []

    # Stage 2 — rerank
    try:
        reranked = await rerank_context(
            decision_model=deps.decision_model,
            query=request.message,
            candidates=raw_chunks,
            keep_top_k=_QA_TOP_K_RERANK,
        )
        top_records = [rc.record for rc in reranked]
    except Exception:
        logger.exception("Rerank failed; using raw retrieval order")
        top_records = raw_chunks[:_QA_TOP_K_RERANK]

    # Stage 3 — generate
    system = build_chat_qa_system_prompt(request.map_snapshot)
    user_msg = build_chat_qa_user_prompt(request.message, request.history, top_records)

    try:
        response = await deps.llm.generate(
            [Message(role="user", content=user_msg)],
            system=system,
            max_tokens=_QA_MAX_TOKENS,
            temperature=0.3,
        )
        answer_text = response.text or ""
    except Exception:
        logger.exception("LLM generation failed during QA")
        answer_text = "I encountered an error while generating an answer. Please try again."

    # Stage 4 — extract citations
    valid_node_ids = {str(n.id) for n in request.map_snapshot.nodes}
    cited_node_ids: list[UUID] = []
    for match in _NODE_CITE_RE.finditer(answer_text):
        uid = match.group(1)
        if uid in valid_node_ids:
            cited_node_ids.append(UUID(uid))

    source_indices = {int(m.group(1)) for m in _SOURCE_CITE_RE.finditer(answer_text)}
    source_refs: list[str] = []
    for i, record in enumerate(top_records, 1):
        if i in source_indices:
            ref = record.source_url or record.source_title or record.source or f"source-{i}"
            if ref:
                source_refs.append(ref)

    return ChatTurnResponse(
        intent=ChatIntent.QUESTION,
        answer=answer_text,
        cited_node_ids=cited_node_ids,
        source_refs=source_refs,
    )


async def handle_small_talk(
    *,
    deps: GenerationDeps,
    request: ChatTurnRequest,
) -> ChatTurnResponse:
    """Return a natural conversational reply without touching Pinecone."""
    system = build_small_talk_system_prompt()
    user_msg = build_small_talk_user_prompt(request.message, request.history)

    try:
        response = await deps.llm.generate(
            [Message(role="user", content=user_msg)],
            system=system,
            max_tokens=256,
            temperature=0.7,
        )
        answer_text = response.text or "Hello! How can I help you with your mind map?"
    except Exception:
        logger.exception("LLM generation failed during small talk")
        answer_text = "Hello! How can I help you with your mind map?"

    return ChatTurnResponse(
        intent=ChatIntent.SMALL_TALK,
        answer=answer_text,
    )
