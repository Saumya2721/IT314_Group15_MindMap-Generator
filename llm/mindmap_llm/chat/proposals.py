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

import logging
from uuid import UUID

from chat.prompts import build_proposal_system_prompt, build_proposal_user_prompt
from contracts.models import ActionProposal, ChatIntent, ChatTurnRequest, ChatTurnResponse
from orchestration.deps import GenerationDeps
from orchestration.prompts import JSONExtractionError, extract_json_object
from providers.base import Message

logger = logging.getLogger(__name__)

_PROPOSAL_MAX_TOKENS = 512


async def build_action_proposal(
    *,
    deps: GenerationDeps,
    request: ChatTurnRequest,
    intent: ChatIntent,
) -> ChatTurnResponse:
    """Ask the LLM to describe the requested map change as structured JSON.

    Returns a ChatTurnResponse with an ActionProposal (never a direct
    mutation).  When the request is ambiguous or references unknown nodes,
    sets needs_clarification=True.
    """
    system = build_proposal_system_prompt(request.map_snapshot)
    user_msg = build_proposal_user_prompt(request.message, request.history)

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
        node_list = ", ".join(f"{n.label} ({n.id})" for n in request.map_snapshot.nodes[:10])
        q = (
            f"I couldn't find the following node(s): {', '.join(invalid_ids)}. "
            f"The available nodes are: {node_list}. Which one did you mean?"
        )
        return ChatTurnResponse(
            intent=intent,
            needs_clarification=True,
            clarification_question=q,
        )

    # Determine effective action type — use the LLM's suggestion if valid,
    # otherwise fall back to the classifier's intent.
    raw_action = str(parsed.get("action_type", "")).strip()
    try:
        action_type = ChatIntent(raw_action)
    except ValueError:
        action_type = intent

    proposal = ActionProposal(
        action_type=action_type,
        target_node_ids=resolved_ids,
        parameters=parsed.get("parameters") or {},
        explanation=str(parsed.get("explanation") or ""),
    )

    return ChatTurnResponse(
        intent=intent,
        action_proposal=proposal,
    )
