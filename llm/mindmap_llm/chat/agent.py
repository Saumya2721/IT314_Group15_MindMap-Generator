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

import asyncio
import logging
import time

from chat.intents import classify_intent
from chat.proposals import build_action_proposal
from chat.qa import answer_question, handle_small_talk
from chat.research import ResearchInvocationGuard
from contracts.models import ChatIntent, ChatTurnRequest, ChatTurnResponse
from orchestration.deps import GenerationDeps

logger = logging.getLogger(__name__)

# Strict conversational latency budget: total chat-turn latency must be <= 5.0 seconds
TOTAL_CHAT_TURN_TIMEOUT_S = 5.0


async def _execute_chat_turn_internal(
    deps: GenerationDeps,
    request: ChatTurnRequest,
    guard: ResearchInvocationGuard,
) -> ChatTurnResponse:
    # Step 1: classify intent (decision model only — fast, typed)
    intent = await classify_intent(deps.decision_model, request.message, request.map_snapshot)
    logger.info("Chat intent classified as %r for map %s", intent, request.map_id)

    # Step 2: dispatch with shared per-turn research invocation guard
    match intent:
        case ChatIntent.QUESTION:
            return await answer_question(deps=deps, request=request, guard=guard)

        case ChatIntent.SMALL_TALK:
            return await handle_small_talk(deps=deps, request=request)

        case _:
            # edit_proposal, regen_whole, regen_node — and safety net for
            # any future StrEnum values added before this match is updated.
            return await build_action_proposal(deps=deps, request=request, intent=intent, guard=guard)


async def run_chat_turn(
    deps: GenerationDeps,
    request: ChatTurnRequest,
    *,
    timeout_s: float = TOTAL_CHAT_TURN_TIMEOUT_S,
) -> ChatTurnResponse:
    """Process one chat turn and return a structured response.

    Enforces:
      - At most ONE research invocation per turn via ResearchInvocationGuard.
      - Total chat-turn latency budget <= 5.0s.
      - Graceful degradation on timeouts or provider failures.

    Args:
        deps: The GenerationDeps bundle (LLM, decision model, vector store, etc.).
        request: The incoming chat turn (message, history, map snapshot).
        timeout_s: Overall execution timeout in seconds (default: 5.0s).

    Returns:
        ChatTurnResponse — always. Never raises unhandled exceptions.
    """
    start_time = time.perf_counter()
    guard = ResearchInvocationGuard(max_invocations=1)

    try:
        response = await asyncio.wait_for(
            _execute_chat_turn_internal(deps, request, guard),
            timeout=timeout_s,
        )
        elapsed = time.perf_counter() - start_time
        logger.info("Chat turn completed in %.2fs (research invocations: %d)", elapsed, guard.invocations)
        return response
    except TimeoutError:
        elapsed = time.perf_counter() - start_time
        logger.warning("Chat turn exceeded latency budget of %.1fs (took %.2fs)", timeout_s, elapsed)
        return ChatTurnResponse(
            intent=ChatIntent.QUESTION,
            answer="I apologize, but gathering the research and answering took longer than expected. Please try again or ask a more specific question.",
        )
    except Exception as exc:
        logger.exception("Unexpected error during chat turn: %s", exc)
        return ChatTurnResponse(
            intent=ChatIntent.QUESTION,
            needs_clarification=True,
            clarification_question="An unexpected error occurred while processing your request. Could you please rephrase it?",
        )
