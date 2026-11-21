"""
Multi-tool targeted query planner for research agent subgraph (S3-A2).

Generates specialized search queries per provider:
  - Tavily (web): broad trends, industry applications, recent developments.
  - Wikipedia: core conceptual definitions, history, taxonomy.
  - Scholar: academic literature, empirical studies, mathematical mechanisms.

Handles:
  - Deduplication against previous search rounds.
  - Robust JSON extraction with regex line-based fallback.
  - Deterministic heuristic fallback when LLM is unavailable or unparseable.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from providers.base import LLMProvider, Message
from tracing import trace_span

logger = logging.getLogger(__name__)

PLANNER_SYSTEM_PROMPT = """\
You are an expert research query planner for an AI mind map generator.
Given a topic or seed material, produce targeted search queries tailored specifically for three distinct research tools:
1. "tavily" (web search): Focus on broad real-world applications, industry trends, and modern implementations.
2. "wikipedia": Focus on foundational concepts, formal definitions, historical context, and taxonomy.
3. "scholar": Focus on academic literature, peer-reviewed research, empirical studies, and theoretical mechanisms.

Output Requirements:
- Return ONLY a JSON object mapping tool names to arrays of strings:
  {
    "tavily": ["query 1", "query 2"],
    "wikipedia": ["query 1"],
    "scholar": ["query 1", "query 2"]
  }
- Each query must be 2 to 8 words, specific and targeted.
- Do NOT output conversational prose, explanations, or markdown fences.
"""

TOOL_ALIASES = {
    "web_search": "tavily",
    "tavily": "tavily",
    "wikipedia": "wikipedia",
    "scholar": "scholar",
    "arxiv_search": "scholar",
    "arxiv": "scholar",
}


def canonical_tool_name(tool_name: str) -> str:
    """Normalize tool identifier to one of ('tavily', 'wikipedia', 'scholar')."""
    return TOOL_ALIASES.get(tool_name.lower().strip(), tool_name.lower().strip())


def build_fallback_queries(
    seed_text: str,
    active_tools: list[str],
    round_num: int = 1,
) -> dict[str, list[str]]:
    """Deterministic heuristic fallback when LLM query planning fails or times out."""
    clean = re.sub(r"[#*_`\n]", " ", seed_text).strip()
    words = [w for w in clean.split() if len(w) > 2][:8]
    topic_core = " ".join(words) if words else "mind map concepts"

    queries_by_tool: dict[str, list[str]] = {}
    for tool in active_tools:
        canon = canonical_tool_name(tool)
        if canon == "wikipedia":
            q = f"{topic_core} overview" if round_num == 1 else f"{topic_core} history and taxonomy"
            queries_by_tool[tool] = [q]
        elif canon in ("scholar", "arxiv_search"):
            q = f"{topic_core} empirical study" if round_num == 1 else f"{topic_core} theoretical mechanisms"
            queries_by_tool[tool] = [q]
        else:  # tavily / web_search
            q = f"{topic_core} key concepts" if round_num == 1 else f"{topic_core} applications and developments"
            queries_by_tool[tool] = [q]

    return queries_by_tool


@trace_span("plan_targeted_queries", run_type="chain")
async def plan_targeted_queries(
    *,
    llm: LLMProvider,
    seed_text: str,
    active_tools: list[str],
    current_round: int = 1,
    executed_queries: list[str] | None = None,
    sufficiency_feedback: str | None = None,
    max_queries_per_tool: int = 2,
    max_tokens: int = 400,
) -> dict[str, list[str]]:
    """Generate specialized queries per tool, avoiding previously executed queries."""
    if not active_tools:
        return {}

    executed_set = {q.strip().lower() for q in (executed_queries or [])}
    tools_str = ", ".join(f"'{canonical_tool_name(t)}'" for t in active_tools)

    prompt_parts = [
        f"Topic / Seed Material:\n{seed_text.strip()[:2000]}",
        "",
        f"Generate up to {max_queries_per_tool} queries for each of the following tools: {tools_str}.",
    ]

    if current_round > 1 and executed_queries:
        prompt_parts.extend(
            [
                "",
                "Previous search queries already completed (DO NOT repeat these):",
                *[f"- {q}" for q in executed_queries[-6:]],
            ]
        )

    if sufficiency_feedback:
        prompt_parts.extend(
            [
                "",
                "Specific knowledge gaps to address in this round:",
                sufficiency_feedback,
            ]
        )

    prompt = "\n".join(prompt_parts)

    try:
        response = await llm.generate(
            [Message(role="user", content=prompt)],
            system=PLANNER_SYSTEM_PROMPT,
            max_tokens=max_tokens,
            temperature=0.3,
        )
        text = (response.text or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        parsed: dict[str, Any] = {}
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                candidate = json.loads(match.group(0))
                if isinstance(candidate, dict):
                    parsed = candidate
            except Exception:
                pass

        planned: dict[str, list[str]] = {}
        for tool in active_tools:
            canon = canonical_tool_name(tool)
            # Find matching list in parsed JSON by tool name or alias
            raw_list = parsed.get(canon) or parsed.get(tool) or []
            if not isinstance(raw_list, list):
                raw_list = [str(raw_list)]

            valid_queries: list[str] = []
            for item in raw_list:
                q = str(item).strip()
                if q and q.lower() not in executed_set:
                    valid_queries.append(q)
                    executed_set.add(q.lower())
                    if len(valid_queries) >= max_queries_per_tool:
                        break

            # If model omitted this tool, use a targeted fallback
            if not valid_queries:
                fallback_map = build_fallback_queries(seed_text, [tool], round_num=current_round)
                valid_queries = [fb for fb in fallback_map.get(tool, []) if fb.lower() not in executed_set]

            planned[tool] = valid_queries[:max_queries_per_tool]

        return planned

    except Exception as exc:
        logger.warning("Query planner LLM call failed; using deterministic fallback: %s", exc)
        return build_fallback_queries(seed_text, active_tools, round_num=current_round)
