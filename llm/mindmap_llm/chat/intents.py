"""
Intent classification for the chat turn agent.

Uses DecisionModel.classify() — the fast "System One" typed-decision
primitive — with the five chat intent labels.  The generation LLM is
NOT called here; that keeps routing cheap, deterministic, and mockable.

If the decision model is unavailable (DecisionModelUnavailable), the
function falls back to ChatIntent.QUESTION so the pipeline degrades
gracefully (NFR-07) rather than crashing.
"""

from __future__ import annotations

import logging

from contracts.models import ChatIntent, MapSnapshot
from providers.decision_model import DecisionModel, DecisionModelUnavailable

logger = logging.getLogger(__name__)

_INTENT_LABELS: list[str] = [e.value for e in ChatIntent]

_INTENT_QUESTION = "What does the user want to do with their mind map? Choose the best matching intent."


async def classify_intent(
    decision_model: DecisionModel,
    message: str,
    map_snapshot: MapSnapshot,
) -> ChatIntent:
    """Return the ChatIntent for *message*.

    *map_snapshot* provides context (node labels) so the decision model
    can distinguish "explain this node" (question) from "redo this node"
    (regen_node).
    """
    node_summary = ", ".join(n.label for n in map_snapshot.nodes[:15])
    state = f"User message: {message}\nMap nodes: {node_summary}" if node_summary else f"User message: {message}"

    try:
        result = await decision_model.classify(
            state=state,
            question=_INTENT_QUESTION,
            labels=_INTENT_LABELS,
        )
        label = result.label.strip().lower()
        if label not in _INTENT_LABELS:
            logger.warning("Decision model returned unknown label %r; defaulting to 'question'", label)
            return ChatIntent.QUESTION
        return ChatIntent(label)
    except DecisionModelUnavailable:
        logger.warning("Decision model unavailable during intent classification; defaulting to 'question'")
        return ChatIntent.QUESTION
    except Exception:
        logger.exception("Unexpected error during intent classification; defaulting to 'question'")
        return ChatIntent.QUESTION
