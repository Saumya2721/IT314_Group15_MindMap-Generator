"""
Main entry point for the S2-L8 chat turn agent.

run_chat_turn() is the single public function.  It:
  1. Classifies the user's intent (via DecisionModel — no LLM call).
  2. Dispatches to the appropriate handler based on that intent.
  3. Returns a ChatTurnResponse — it never modifies the map directly.

Architecture note — why NOT a LangGraph graph:
  The generation pipeline uses LangGraph because it has a retry loop
  (validate → back to generate on bad JSON).  The chat agent has no
  retry loop; it's a linear async function that branches on intent.
  Using LangGraph here would add boilerplate with no benefit.
  This follows the same reasoning as the S2-L7 branch-scoped helpers.
"""

from __future__ import annotations

import logging

from chat.intents import classify_intent
from chat.proposals import build_action_proposal
from chat.qa import answer_question, handle_small_talk
from contracts.models import ChatIntent, ChatTurnRequest, ChatTurnResponse
from orchestration.deps import GenerationDeps

logger = logging.getLogger(__name__)


async def run_chat_turn(
    deps: GenerationDeps,
    request: ChatTurnRequest,
) -> ChatTurnResponse:
    """Process one chat turn and return a structured response.

    Args:
        deps: The same GenerationDeps bundle used by the generation
              pipeline — LLM, decision model, vector store, embeddings.
        request: The incoming chat turn (message, history, map snapshot).

    Returns:
        ChatTurnResponse — always.  Never raises; errors become
        needs_clarification=True responses so the caller always gets
        a usable value.
    """
    # Step 1: classify intent (decision model only — fast, typed)
    intent = await classify_intent(deps.decision_model, request.message, request.map_snapshot)
    logger.info("Chat intent classified as %r for map %s", intent, request.map_id)

    # Step 2: dispatch
    match intent:
        case ChatIntent.QUESTION:
            return await answer_question(deps=deps, request=request)

        case ChatIntent.SMALL_TALK:
            return await handle_small_talk(deps=deps, request=request)

        case _:
            # edit_proposal, regen_whole, regen_node — and safety net for
            # any future StrEnum values added before this match is updated.
            return await build_action_proposal(deps=deps, request=request, intent=intent)
