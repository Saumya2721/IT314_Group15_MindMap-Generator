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

import asyncio
import logging
import os
import re
from uuid import UUID

from chat.context import assemble_chat_context
from chat.prompts import (
    build_chat_qa_system_prompt,
    build_chat_qa_user_prompt,
    build_small_talk_system_prompt,
    build_small_talk_user_prompt,
)
from chat.research import (
    RESEARCH_TOOL_SPEC,
    ResearchInvocationGuard,
    format_research_context,
    invoke_chat_research,
    should_conduct_research,
)
from chat.token_budget import TokenBudget, estimate_tokens
from contracts.models import ChatIntent, ChatTurnRequest, ChatTurnResponse
from orchestration.context_discovery import get_model_context_window
from orchestration.deps import GenerationDeps
from providers.base import Message
from rag.retrieval import rerank_context, retrieve_context
from research.models import ResearchSource

logger = logging.getLogger(__name__)

_QA_TOP_K_RETRIEVE = 10
_QA_TOP_K_RERANK = 5
_QA_MAX_TOKENS = int(os.getenv("CHAT_QA_MAX_TOKENS", "4096"))

# Pattern to extract [node:<uuid>] citations from LLM output.
_NODE_CITE_RE = re.compile(r"\[node:([0-9a-fA-F-]{36})\]")
# Pattern to extract [<number>] source citations.
_SOURCE_CITE_RE = re.compile(r"\[(\d+)\]")


async def answer_question(
    *,
    deps: GenerationDeps,
    request: ChatTurnRequest,
    guard: ResearchInvocationGuard | None = None,
) -> ChatTurnResponse:
    """Return a grounded answer for a 'question' intent turn."""
    namespace = str(request.map_id)
    inv_guard = guard or ResearchInvocationGuard(max_invocations=1)

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

    # Stage 2.5 — Check if fresh external research is warranted
    research_sources: list[ResearchSource] = []
    research_refs: list[str] = []
    needs_research = await should_conduct_research(deps.decision_model, request.message, request.map_snapshot)
    if needs_research and inv_guard.can_invoke:
        user_id = (request.memory_context or {}).get("user_id") if request.memory_context else None
        res_sources, res_refs, _warnings = await invoke_chat_research(
            deps=deps,
            query=request.message,
            map_id=request.map_id,
            guard=inv_guard,
            user_id=user_id,
        )
        research_sources.extend(res_sources)
        research_refs.extend(res_refs)

    # Stage 3 — generate
    system = build_chat_qa_system_prompt(request.map_snapshot)

    # S3-L3: Inject user memories from memory_context into the system prompt.
    # Lazy import to avoid circular dependency: chat → memory → chat
    from memory.prompt import inject_memories_into_system_prompt  # noqa: PLC0415

    long_term_memories = (request.memory_context or {}).get("long_term_memories", [])
    if long_term_memories:
        system = inject_memories_into_system_prompt(
            system,
            long_term_memories,
            provider=deps.settings.llm.provider,
            model=deps.settings.llm.model,
        )

    active = deps.dynamic_config.get_config().llm if deps.dynamic_config else None
    model_context = await asyncio.to_thread(
        get_model_context_window,
        active.model if active else deps.settings.llm.model,
        active.provider if active else deps.settings.llm.provider,
        api_base=active.api_base if active else None,
    )
    budget = TokenBudget(
        max_context_tokens=model_context,
        reserved_response_tokens=_QA_MAX_TOKENS,
    )

    def render_prompt():
        research_context = format_research_context(research_sources) if research_sources else None
        records = list(top_records)
        history = request.history
        summary = request.summary
        while True:
            assembled = assemble_chat_context(
                current_message=request.message,
                history=history,
                summary=summary,
                budget=budget,
                system_prompt=system,
                overhead_tokens=sum(estimate_tokens(c.chunk_text) for c in records) + estimate_tokens(research_context),
            )
            prompt = build_chat_qa_user_prompt(
                request.message,
                assembled.recent_messages,
                records,
                summary=assembled.summary,
                research_context=research_context,
                selected_node=request.effective_selected_node,
            )
            if estimate_tokens(system) + estimate_tokens(prompt) + 64 <= budget.available_input_tokens:
                return prompt, records
            if records:
                records.pop()
            elif history or summary:
                history, summary = [], None
            elif research_context:
                research_context = None
            else:
                raise ValueError("The map and question exceed the provider context budget.")

    tools_to_offer = [RESEARCH_TOOL_SPEC] if inv_guard.can_invoke else None

    cited_records = []
    try:
        user_msg, cited_records = render_prompt()
        response = await deps.llm.generate(
            [Message(role="user", content=user_msg)],
            system=system,
            tools=tools_to_offer,
            max_tokens=_QA_MAX_TOKENS,
            temperature=0.3,
        )
        answer_text = response.text or ""

        # Handle tool call if model requests research dynamically
        if response.tool_calls:
            for tc in response.tool_calls:
                if tc.name == "research_topic" and inv_guard.can_invoke:
                    tool_query = str(tc.arguments.get("query", request.message))
                    user_id = (request.memory_context or {}).get("user_id") if request.memory_context else None
                    tc_sources, tc_refs, _ = await invoke_chat_research(
                        deps=deps,
                        query=tool_query,
                        map_id=request.map_id,
                        guard=inv_guard,
                        user_id=user_id,
                    )
                    if tc_sources:
                        research_sources.extend(tc_sources)
                        research_refs.extend(tc_refs)

                    # Always synthesize follow-up answer after tool invocation (even if 0 external sources)
                    follow_up_user_msg, cited_records = render_prompt()
                    follow_up_resp = await deps.llm.generate(
                        [Message(role="user", content=follow_up_user_msg)],
                        system=system,
                        max_tokens=_QA_MAX_TOKENS,
                        temperature=0.3,
                    )
                    answer_text = follow_up_resp.text or ""
                    break

        # Defensive fallback: ensure answer_text is never empty
        if not answer_text.strip():
            logger.warning("Empty QA answer text after initial generation, forcing text generation without tools")
            fallback_user_msg, cited_records = render_prompt()
            fallback_resp = await deps.llm.generate(
                [Message(role="user", content=fallback_user_msg)],
                system=system,
                tools=None,
                max_tokens=_QA_MAX_TOKENS,
                temperature=0.3,
            )
            answer_text = fallback_resp.text or ""

        if not answer_text.strip():
            answer_text = (
                "I reviewed the mind map regarding your question, but could not synthesize a detailed answer. "
                "Could you please rephrase or specify which branch you are exploring?"
            )
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
    for i, record in enumerate(cited_records, 1):
        if i in source_indices:
            ref = record.source_url or record.source_title or record.source or f"source-{i}"
            if ref and ref not in source_refs:
                source_refs.append(ref)

    # Add citations from external research
    for ref in research_refs:
        if ref and ref not in source_refs:
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
    assembled = assemble_chat_context(
        current_message=request.message,
        history=request.history,
        summary=request.summary,
        budget=TokenBudget(reserved_response_tokens=256),
        system_prompt=system,
    )
    user_msg = build_small_talk_user_prompt(
        request.message,
        assembled.recent_messages,
        summary=assembled.summary,
    )

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
