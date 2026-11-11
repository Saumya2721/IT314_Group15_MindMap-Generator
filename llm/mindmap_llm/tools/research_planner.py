"""
Multi-query search planner and parallel execution engine with strict quota protection (FR-41).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Sequence

from providers.base import LLMProvider, Message
from tools.base import Tool, ToolError, ToolResult

logger = logging.getLogger(__name__)

RESEARCH_PLANNER_SYSTEM_PROMPT = """\
You are a search query planner for a mind map generator.
Given a user's seed topic or material, generate up to {n} distinct, concise, targeted search queries that together explore the key facets, subtopics, and background needed to build a comprehensive mind map.

Output Requirements:
- Return ONLY a JSON list of strings (e.g. ["query 1", "query 2", "query 3"]).
- No conversational prose, no explanations, no markdown fences.
- Each query should be 2 to 8 words long.
- Cover different angles: core concepts, mechanisms/components, applications, and current developments.
"""


async def plan_research_queries(
    *,
    llm: LLMProvider,
    seed_text: str,
    n_queries: int = 4,
    max_tokens: int = 200,
) -> list[str]:
    """Ask LLM to generate n focused sub-queries from seed.
    Falls back to [seed_text[:200]] on failure.
    """
    clean_seed = seed_text.strip()
    fallback = [clean_seed[:200]] if clean_seed else ["general overview"]
    if n_queries <= 1:
        return fallback

    prompt = (
        f"Topic / Seed Material:\n{clean_seed[:1500]}\n\n"
        f"Generate {n_queries} diverse search sub-queries as a JSON array of strings:"
    )

    try:
        response = await llm.generate(
            [Message(role="user", content=prompt)],
            system=RESEARCH_PLANNER_SYSTEM_PROMPT.format(n=n_queries),
            max_tokens=max_tokens,
            temperature=0.3,
        )
        text = (response.text or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, list):
                queries = [str(q).strip() for q in parsed if str(q).strip()]
                if queries:
                    return queries[:n_queries]

        # Regex line-based fallback if JSON formatting is slightly off
        lines = [re.sub(r"^[-*\d.\s]+", "", line).strip() for line in text.splitlines() if line.strip()]
        valid_lines = [line for line in lines if line and not line.startswith("[") and not line.startswith("{")]
        if valid_lines:
            return valid_lines[:n_queries]

        logger.warning("Could not extract sub-queries from LLM response: %r, using fallback", text)
        return fallback
    except Exception as exc:
        logger.warning("Query planner LLM call failed, falling back to seed prefix: %s", exc)
        return fallback


async def execute_research_queries(
    *,
    tool: Tool,
    queries: Sequence[str],
    max_queries: int = 3,
) -> list[ToolResult]:
    """Execute up to max_queries in parallel, deduplicate results by URL."""
    queries_to_run = [q for q in queries if q.strip()][:max_queries]
    if not queries_to_run:
        return []

    async def _safe_invoke(query_str: str) -> list[ToolResult]:
        try:
            return await tool.invoke(query=query_str)
        except ToolError as exc:
            logger.warning("Tool query %r failed: %s", query_str, exc)
            return []
        except Exception as exc:
            logger.warning("Tool query %r raised unexpected error: %s", query_str, exc, exc_info=True)
            return []

    tasks = [_safe_invoke(q) for q in queries_to_run]
    batches = await asyncio.gather(*tasks)

    # URL-based deduplication preserving discovery order
    seen_urls: set[str] = set()
    deduped: list[ToolResult] = []
    for batch in batches:
        for result in batch:
            if result.source_url:
                if result.source_url in seen_urls:
                    continue
                seen_urls.add(result.source_url)
            deduped.append(result)

    return deduped
