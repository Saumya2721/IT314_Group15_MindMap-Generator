"""
Intent classification for the chat turn agent.

Routes user messages into one of the five ChatIntent categories:
  - regen_node
  - regen_whole
  - edit_proposal
  - question
  - small_talk

Architecture:
1. Fast-path deterministic regexes for unambiguous zero-latency matches.
2. Decision model classification for semantic, multi-clause, or typo-containing messages:
   - When _USE_JEV_CLASSIFY = True: attempts Jev DecisionModel.classify() first
     (fast System One primitive when Jev is healthy).
   - When _USE_JEV_CLASSIFY = False: bypasses Jev to avoid 502 Bad Gateway timeouts
     and routes directly to the LLM-backed decision fallback.
3. Graceful degradation: any failure defaults to ChatIntent.QUESTION.
"""

from __future__ import annotations

import logging
import re

from contracts.models import ChatIntent, MapSnapshot
from providers.base import LLMProvider, Message
from providers.decision_model import DecisionModel, DecisionModelUnavailable
from providers.registry import SafeDecisionModel

logger = logging.getLogger(__name__)

_INTENT_LABELS: list[str] = [e.value for e in ChatIntent]

# ---------------------------------------------------------------------------
# Flag: set True to attempt Jev classify before the LLM fallback.
# When Jev is healthy this saves ~300-500 ms of LLM latency.
# Set False because jevai.org currently returns 502 Bad Gateway.
# Toggle to True when Jev service is restored.
# ---------------------------------------------------------------------------
_USE_JEV_CLASSIFY: bool = False

# ---------------------------------------------------------------------------
# Fast-path regexes
# ---------------------------------------------------------------------------

# A map-wide retry may have a second clause that changes the requested action.
_MAP_RETRY_RE = re.compile(
    r"^(?:please\s+)?(?:(?:can|could|would) you\s+)?(?:please\s+)?"
    r"(?:generate(?:\s+(?:it|(?:the |this )?(?:whole |entire )?(?:map|mind\s*map|graph)))?\s+again|"
    r"(?:re-?generate|redo|rebuild)\s+(?:it|everything|(?:the |this )?(?:whole |entire )?(?:map|mind\s*map|graph)))\b",
    re.IGNORECASE,
)

_MORE_BRANCHES_RE = re.compile(
    r"[\s,:-]*(?:(?:but|and|with|this time|keeping in mind that)\s+)*"
    r"(?:(?:please|you should)\s+)?(?:(?:add|create|include)\s+)?"
    r"(?:more|additional)\s+(?:sub[ -]?)?branches[.!? ]*",
    re.IGNORECASE,
)

_REGEN_WHOLE_RE = re.compile(
    r"\b(re-?generate|redo|re-?create|rebuild)\s+(the\s+)?(whole|entire|all|full)\s+(map|graph|mind\s*map)\b|"
    r"\b(re-?generate|redo|re-?create|rebuild)\s+(everything|the\s+whole\s+thing)\b",
    re.IGNORECASE,
)

_REGEN_NODE_RE = re.compile(
    r"\b(re-?generate|redo|re-?create|re-?write|expand|flesh\s*out)\s+(the\s+)?([a-z0-9\s_-]+?\s+)?(branch|node|section|sub-?topic)\b|"
    r"\b(re-?generate|redo|re-?create|re-?write|expand)\s+(the\s+)?(branch|node|section)\s+([a-z0-9\s_-]+)",
    re.IGNORECASE,
)

_EDIT_PROPOSAL_RE = re.compile(
    r"\b(add|insert|create|delete|remove|connect|link|disconnect|unlink|rename)\s+(a\s+|an\s+|the\s+)?(new\s+)?(node|edge|branch|connection|link)\b|"
    r"\b(add|insert)\s+.+?\s+(to|into|under)\s+(the\s+)?(map|node|branch)\b",
    re.IGNORECASE,
)

_SMALL_TALK_RE = re.compile(
    r"^\s*(hi|hello|hey|greetings|howdy|good\s+(morning|afternoon|evening)|thanks|thank\s+you|thx|bye|goodbye)[!.? ]*$",
    re.IGNORECASE,
)


_EXPLAIN_CONVERSATIONAL_RE = re.compile(
    r"\bexpand\s+on\s+(your|the|this)?\s*(explanation|answer|response|thought|reasoning)\b",
    re.IGNORECASE,
)


def _matches_node_regeneration(message: str, map_snapshot: MapSnapshot) -> bool:
    """Check if the user is asking to regenerate/redo/expand a known node on the map."""
    msg_lower = message.lower()
    if _EXPLAIN_CONVERSATIONAL_RE.search(msg_lower):
        return False
    has_regen_verb = bool(
        re.search(r"\b(re-?generate|redo|re-?create|re-?write|expand|flesh\s*out)\b", msg_lower)
    )
    if not has_regen_verb:
        return False
    for node in map_snapshot.nodes:
        lbl = node.label.strip().lower()
        if len(lbl) >= 3 and lbl in msg_lower:
            return True
    return False


# ---------------------------------------------------------------------------
# Structured classification prompt for DecisionModel / LLM fallback
# ---------------------------------------------------------------------------
_INTENT_QUESTION = """Choose one label for what the user wants you to do on this turn.
Read the whole message, including instructions after "again", "but", or "keeping in mind".
Use map labels to resolve references. A mentioned node alone does not imply a request to change it.

edit_proposal: The user specifies a change to the map's contents or structure, such as adding
children, removing a topic, or reconnecting nodes. A retry with a concrete edit belongs here.
"Generate again keeping in mind that each topic needs more subbranches" -> edit_proposal
"Could you redo it, splitting Storage into local and cloud?" -> edit_proposal

regen_whole: The user wants a fresh version of the entire map. General preferences such as
"more detail" can guide regeneration. "Generate again" or "Redo the whole map in more detail" -> regen_whole

regen_node: The user wants one existing branch or node regenerated or expanded, leaving its
specific edits up to the assistant. "Exppand the Storage branch and explain the additions" -> regen_node

question: The requested result is an answer, explanation, or instructions for the user.
"How do I add more subbranches?" or "Expand on your explanation of Storage" -> question

small_talk: The message is solely a greeting, thanks, farewell, or casual chatter.

Treat polite requests and minor misspellings according to their meaning. When the user requests
both a map change and an explanation, classify the change. Return only the label."""


async def classify_intent(
    decision_model: DecisionModel,
    message: str,
    map_snapshot: MapSnapshot,
    *,
    llm: LLMProvider | None = None,
) -> ChatIntent:
    """Return the ChatIntent for *message*.

    Classification order:
    1. Zero-cost deterministic regex fast-paths (regen_whole, regen_node, edit_proposal, small_talk).
    2. Contextual decision model (Jev when _USE_JEV_CLASSIFY is True; direct LLM fallback when False).
    3. Safe default fallback to ChatIntent.QUESTION on any unexpected failure.
    """
    clean_msg = message.strip()

    # ── 1. Deterministic pattern checks ──────────────────────────────────────
    retry = _MAP_RETRY_RE.match(clean_msg)
    if retry:
        instructions = clean_msg[retry.end():].strip()
        if instructions.rstrip(".!? ").lower() in {"", "with more detail", "in more detail", "from scratch"}:
            return ChatIntent.REGEN_WHOLE
        if _MORE_BRANCHES_RE.fullmatch(instructions):
            return ChatIntent.EDIT_PROPOSAL
        # Let the decision model read the full retry and its qualifications.
    else:
        if _REGEN_WHOLE_RE.search(clean_msg):
            logger.info("Deterministic intent match: REGEN_WHOLE for message %r", clean_msg)
            return ChatIntent.REGEN_WHOLE

        if not _EXPLAIN_CONVERSATIONAL_RE.search(clean_msg) and (
            _REGEN_NODE_RE.search(clean_msg) or _matches_node_regeneration(clean_msg, map_snapshot)
        ):
            logger.info("Deterministic intent match: REGEN_NODE for message %r", clean_msg)
            return ChatIntent.REGEN_NODE

        if _EDIT_PROPOSAL_RE.search(clean_msg):
            logger.info("Deterministic intent match: EDIT_PROPOSAL for message %r", clean_msg)
            return ChatIntent.EDIT_PROPOSAL

    if _SMALL_TALK_RE.match(clean_msg):
        logger.info("Deterministic intent match: SMALL_TALK for message %r", clean_msg)
        return ChatIntent.SMALL_TALK

    # ── 2. Contextual decision model classification ──────────────────────────
    node_summary = ", ".join(n.label for n in map_snapshot.nodes[:15])
    mentioned_nodes = [
        n.label for n in map_snapshot.nodes if len(n.label) >= 3 and n.label.lower() in clean_msg.lower()
    ]
    context_lines = [f"User message: {clean_msg}"]
    if mentioned_nodes:
        context_lines.append(f"Mentioned node(s) on map: {', '.join(mentioned_nodes[:5])}")
    if node_summary:
        context_lines.append(f"Map nodes: {node_summary}")
    state = "\n".join(context_lines)

    try:
        # If Jev classify is disabled, bypass Jev on SafeDecisionModel to avoid 502 timeout
        if not _USE_JEV_CLASSIFY and isinstance(decision_model, SafeDecisionModel):
            result = await decision_model._llm_backed.classify(
                state=state,
                question=_INTENT_QUESTION,
                labels=_INTENT_LABELS,
            )
        else:
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
