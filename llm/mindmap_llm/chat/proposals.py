"""
Structured action-proposal builder for the chat turn agent.

For edit_proposal, regen_whole, and regen_node intents the agent returns
an ActionProposal instead of a text answer.  The proposal is NEVER
applied directly — that happens through a separate confirmation flow
(S2-L10 / S2-BE3) after explicit user approval.

Design decisions:
  - The LLM outputs a JSON object describing the proposed action.
  - orchestration/prompts.py's extract_json_object() helper is reused
    to pull the JSON block out of the LLM's response.
  - Referenced node IDs are validated against the map snapshot; invalid
    IDs trigger a clarification response rather than a hard failure.
  - If the LLM signals ambiguity (needs_clarification=true in its JSON),
    the agent surfaces that to the caller without raising.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from chat.context import assemble_chat_context
from chat.prompts import build_proposal_system_prompt, build_proposal_user_prompt
from chat.research import (
    ResearchInvocationGuard,
    format_research_context,
    invoke_chat_research,
    should_conduct_research,
)
from chat.token_budget import TokenBudget
from contracts.models import ActionProposal, ChatIntent, ChatTurnRequest, ChatTurnResponse
from orchestration.context_discovery import get_model_context_window
from orchestration.deps import GenerationDeps
from orchestration.prompts import JSONExtractionError, extract_json_object
from providers.base import Message
from research.models import ResearchSource

logger = logging.getLogger(__name__)

_PROPOSAL_MAX_TOKENS = 1024  # raised from 512 — 512 was too tight for multi-node JSON proposals


async def build_action_proposal(
    *,
    deps: GenerationDeps,
    request: ChatTurnRequest,
    intent: ChatIntent,
    guard: ResearchInvocationGuard | None = None,
) -> ChatTurnResponse:
    """Ask the LLM to describe the requested map change as structured JSON.

    Returns a ChatTurnResponse with an ActionProposal (never a direct
    mutation).  When the request is ambiguous or references unknown nodes,
    sets needs_clarification=True.
    """
    inv_guard = guard or ResearchInvocationGuard(max_invocations=1)

    # Check if external research is needed to ground the proposed changes
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

    research_context = format_research_context(research_sources) if research_sources else None

    system = build_proposal_system_prompt(request.map_snapshot)
    # S3-L3: Inject user memories from memory_context into the proposal system prompt.
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
    assembled = assemble_chat_context(
        current_message=request.message,
        history=request.history,
        summary=request.summary,
        budget=TokenBudget(
            max_context_tokens=model_context,
            reserved_response_tokens=_PROPOSAL_MAX_TOKENS,
        ),
        system_prompt=system,
    )
    user_msg = build_proposal_user_prompt(
        request.message,
        assembled.recent_messages,
        summary=assembled.summary,
        research_context=research_context,
        selected_node=request.effective_selected_node,
    )

    # LLM call — single turn, no retry (parse errors become clarifications)
    try:
        response = await deps.llm.generate(
            [Message(role="user", content=user_msg)],
            system=system,
            max_tokens=_PROPOSAL_MAX_TOKENS,
            temperature=0.1,
        )
        raw_text = response.text or ""
    except Exception:
        logger.exception("LLM generation failed while building action proposal")
        return ChatTurnResponse(
            intent=intent,
            needs_clarification=True,
            clarification_question="I had trouble understanding your request. Could you rephrase it?",
        )

    # Parse JSON from LLM response
    try:
        parsed = extract_json_object(raw_text)
    except (JSONExtractionError, ValueError, TypeError):
        logger.warning("Could not parse JSON from proposal LLM response: %r", raw_text[:200])
        return ChatTurnResponse(
            intent=intent,
            needs_clarification=True,
            clarification_question="I wasn't sure how to interpret that request. Could you be more specific?",
        )

    # LLM-signalled ambiguity
    if parsed.get("needs_clarification"):
        return ChatTurnResponse(
            intent=intent,
            needs_clarification=True,
            clarification_question=parsed.get("clarification_question") or "Could you clarify your request?",
        )

    # Resolve and validate target_node_ids
    valid_node_ids = {str(n.id): n.id for n in request.map_snapshot.nodes}
    raw_ids: list[str] = parsed.get("target_node_ids") or []
    resolved_ids: list[UUID] = []
    invalid_ids: list[str] = []

    for raw_id in raw_ids:
        raw_id_str = str(raw_id).strip()
        if raw_id_str in valid_node_ids:
            resolved_ids.append(valid_node_ids[raw_id_str])
        else:
            invalid_ids.append(raw_id_str)

    if invalid_ids:
        node_list = ", ".join(f"**{n.label}** [node:{n.id}]" for n in request.map_snapshot.nodes[:10])
        q = (
            f"I couldn't find the following node(s): {', '.join(invalid_ids)}. "
            f"The available nodes are: {node_list}. Which one did you mean?"
        )
        return ChatTurnResponse(
            intent=intent,
            needs_clarification=True,
            clarification_question=q,
        )

    # Validate update_nodes if present in parameters
    raw_params = parsed.get("parameters") or {}
    if isinstance(raw_params, dict):
        for un in raw_params.get("update_nodes", []):
            if isinstance(un, dict) and "id" in un:
                un_id = str(un["id"]).strip()
                if un_id not in valid_node_ids:
                    return ChatTurnResponse(
                        intent=intent,
                        needs_clarification=True,
                        clarification_question=f"Cannot update node {un_id} because it does not exist on the map.",
                    )

        # Ground create_nodes or update_nodes with research sources if not explicitly provided
        if research_refs:
            for cn in raw_params.get("create_nodes", []):
                if isinstance(cn, dict) and not cn.get("sources"):
                    cn["sources"] = list(research_refs[:3])
            for un in raw_params.get("update_nodes", []):
                if isinstance(un, dict) and not un.get("sources"):
                    un["sources"] = list(research_refs[:3])

    # Determine effective action type — preserve add_node / update_node / edit_proposal
    raw_action = str(parsed.get("action_type", "")).strip()
    action_type: ChatIntent | str
    try:
        action_type = ChatIntent(raw_action)
    except ValueError:
        action_type = raw_action if raw_action in ("add_node", "update_node") else intent

    proposal = ActionProposal(
        action_type=action_type,
        target_node_ids=resolved_ids,
        parameters=raw_params,
        explanation=str(parsed.get("explanation") or ""),
    )

    return ChatTurnResponse(
        intent=intent,
        action_proposal=proposal,
        source_refs=research_refs,
    )
