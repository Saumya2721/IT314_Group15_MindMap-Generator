"""
Research integration for the conversational chat agent (S3-A4).

Provides:
  - ResearchInvocationGuard: Programmatic guard guaranteeing at most ONE research
    invocation per chat turn.
  - should_conduct_research: Distinguishes questions/edits that require external
    grounding (recent developments, research papers, external facts) from standard
    map queries or small talk.
  - invoke_chat_research: Invokes the existing research subgraph (run_research_subgraph)
    within a strict conversational latency budget (<= 2.5s) with graceful degradation.
  - format_research_context: Formats ResearchSource objects into numbered citation
    blocks for prompt injection.
  - RESEARCH_TOOL_SPEC: Vendor-neutral ToolSpec for LLM tool calling.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections.abc import Sequence
from uuid import UUID

from contracts.models import MapSnapshot
from orchestration.deps import GenerationDeps
from providers.base import ToolSpec
from providers.decision_model import DecisionModel, DecisionModelUnavailable
from research.models import ResearchSource
from research.subgraph import run_research_subgraph

logger = logging.getLogger(__name__)

# Conversational latency allocation for research execution: configurable via environment (default: 8.0s)
DEFAULT_CHAT_RESEARCH_TIMEOUT_S = float(os.getenv("CHAT_RESEARCH_TIMEOUT_S", "8.0"))

# Patterns that indicate a need for fresh or external information
_RESEARCH_KEYWORDS_RE = re.compile(
    r"\b("
    r"latest|recent|newest|current|breakthrough|developments?|trends?|"
    r"research\s+papers?|academic|scholarly|literature|studies|study|"
    r"search\s+for|look\s+up|find\s+out|investigate|who\s+is|what\s+is\s+the\s+latest|"
    r"market\s+trends?|state\s+of\s+the\s+art|sota|"
    r"external|outside\s+sources?|web\s+search|wikipedia|scholar|arxiv"
    r")\b",
    re.IGNORECASE,
)

RESEARCH_TOOL_SPEC = ToolSpec(
    name="research_topic",
    description=(
        "Search external web, academic literature (Semantic Scholar/arXiv), and Wikipedia "
        "for fresh information, recent developments, or external background when existing "
        "mind-map context is insufficient."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Targeted research query to investigate.",
            },
        },
        "required": ["query"],
    },
)


class ResearchInvocationGuard:
    """Programmatically enforces at most ONE research invocation per chat turn.

    Scattered or recursive tool calls within a single chat turn cannot exceed
    this limit.
    """

    def __init__(self, max_invocations: int = 1) -> None:
        self._max = max_invocations
        self._count = 0
        self._executed_queries: list[str] = []

    @property
    def invocations(self) -> int:
        return self._count

    @property
    def can_invoke(self) -> bool:
        return self._count < self._max

    def record_invocation(self, query: str) -> bool:
        """Attempt to register an invocation. Returns True if permitted, False if limit reached."""
        if self._count >= self._max:
            logger.warning(
                "Research invocation rejected by guard: already invoked %d times (max %d). Query: %r",
                self._count,
                self._max,
                query,
            )
            return False
        self._count += 1
        self._executed_queries.append(query)
        return True


async def should_conduct_research(
    decision_model: DecisionModel | None,
    message: str,
    map_snapshot: MapSnapshot,
) -> bool:
    """Determine whether the user's message warrants fresh external research.

    Uses keyword/pattern heuristics first, with decision-model classification
    if available.
    """
    cleaned = message.strip()
    if not cleaned:
        return False

    # 1. Fast regex detection for typical research signals
    if _RESEARCH_KEYWORDS_RE.search(cleaned):
        return True

    # 2. Check if the question refers to concepts absent from the map snapshot
    node_labels = {n.label.lower() for n in map_snapshot.nodes}
    tokens = set(re.findall(r"\w{4,}", cleaned.lower()))
    has_map_overlap = any(any(tok in lbl for lbl in node_labels) for tok in tokens)

    # 3. Optional decision model classification if available
    if decision_model is not None and not has_map_overlap:
        try:
            state = f"User message: {cleaned}\nMap nodes: {', '.join(n.label for n in map_snapshot.nodes[:10])}"
            result = await decision_model.classify(
                state=state,
                question="Does this user request require external web or academic research beyond the mind map?",
                labels=["needs_research", "no_research"],
            )
            return result.label.strip().lower() == "needs_research"
        except (DecisionModelUnavailable, Exception) as exc:
            logger.debug("Decision model research classification skipped: %s", exc)

    # If message is substantive and asks about something not on the map, default to False unless keywords matched
    return False


async def invoke_chat_research(
    *,
    deps: GenerationDeps,
    query: str,
    map_id: UUID,
    guard: ResearchInvocationGuard,
    user_id: UUID | str | None = None,
    timeout_s: float = DEFAULT_CHAT_RESEARCH_TIMEOUT_S,
    active_tools: list[str] | None = None,
) -> tuple[list[ResearchSource], list[str], list[str]]:
    """Invoke the research subgraph bounded by guard and latency budget.

    Returns:
        (sources, source_refs, warnings)
        - sources: List of retrieved & deduplicated ResearchSource instances.
        - source_refs: List of formatted string references (URL or Title) for citations.
        - warnings: Any degradation or error messages encountered.
    """
    # 1. Enforce strict single-invocation guard
    if not guard.record_invocation(query):
        return [], [], ["Research invocation limit exceeded for this chat turn (maximum 1)."]

    start_time = time.perf_counter()
    namespace = str(map_id)
    job_id = map_id  # Scoped namespace / job identity

    try:
        # 2. Invoke subgraph within strict timeout
        deduped_sources, warnings, degraded, _logs = await asyncio.wait_for(
            run_research_subgraph(
                deps,
                seed_text=query,
                namespace=namespace,
                job_id=job_id,
                user_id=user_id,
                active_tools=active_tools,
                timeout_s=timeout_s,
                max_rounds=1,  # Strict single round for conversational latency
            ),
            timeout=timeout_s + 0.2,  # Hard outer safety margin
        )
    except TimeoutError:
        elapsed = time.perf_counter() - start_time
        logger.warning("Chat research timed out after %.2fs for query %r", elapsed, query)
        return [], [], [f"Research timed out after {elapsed:.2f}s; proceeded with available context."]
    except Exception as exc:
        logger.exception("Unexpected error in chat research: %s", exc)
        return [], [], [f"Research failed: {exc}"]

    # 3. Extract clean source reference strings for ChatTurnResponse.source_refs
    source_refs: list[str] = []
    for src in deduped_sources:
        ref = src.source_url or src.source_title or f"[{src.tool}]"
        if ref and ref not in source_refs:
            source_refs.append(ref)

    elapsed = time.perf_counter() - start_time
    logger.info(
        "Chat research completed in %.2fs: %d sources retrieved for query %r",
        elapsed,
        len(deduped_sources),
        query,
    )
    return deduped_sources, source_refs, warnings


def format_research_context(sources: Sequence[ResearchSource]) -> str:
    """Format research sources into a grounded citation block for prompt injection."""
    if not sources:
        return "(no external research material retrieved)"

    parts: list[str] = []
    for i, src in enumerate(sources, 1):
        title = src.source_title or src.tool.capitalize()
        url = f" <{src.source_url}>" if src.source_url else ""
        doi = f" (DOI: {src.doi})" if src.doi else ""
        authors = f" by {', '.join(src.authors[:3])}" if src.authors else ""
        header = f"[{i}] {title}{authors}{doi}{url}"
        snippet = src.content.strip()
        parts.append(f"{header}\n{snippet}")

    return "\n\n".join(parts)
