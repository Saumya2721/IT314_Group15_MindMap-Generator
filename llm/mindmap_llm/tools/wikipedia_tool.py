"""
Wikipedia search tool implementation (FR-29, S3-A2).
Queries Wikipedia's API for encyclopedic summaries, conceptual definitions, and foundational background.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx
from providers.base import ToolSpec
from tools.base import Tool, ToolError, ToolResult


class WikipediaTool(Tool):
    """Searches Wikipedia and normalizes article snippets into ToolResult records."""

    def __init__(
        self,
        api_base: str = "https://en.wikipedia.org/w/api.php",
        timeout_s: float = 10.0,
        max_results: int = 4,
    ):
        self._api_base = api_base
        self._timeout_s = timeout_s
        self._max_results = max_results

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="wikipedia",
            description="Search Wikipedia for encyclopedic overviews, historical context, and foundational definitions.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )

    async def invoke(self, *, query: str = "", **kwargs: Any) -> list[ToolResult]:
        query_val = query or str(kwargs.get("query", "")).strip()
        if not query_val:
            return []

        params: dict[str, str | int] = {
            "action": "query",
            "list": "search",
            "srsearch": query_val,
            "srlimit": self._max_results,
            "format": "json",
            "utf8": 1,
        }

        headers = {"User-Agent": "MindMapGenerator/1.0 (academic software engineering project; contact@mindmap.local)"}

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s, follow_redirects=True) as client:
                resp = await client.get(self._api_base, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise ToolError(f"Wikipedia search failed: {exc}") from exc
        except Exception as exc:
            raise ToolError(f"Wikipedia search error: {exc}") from exc

        results: list[ToolResult] = []
        search_items = data.get("query", {}).get("search", [])
        for item in search_items:
            title = item.get("title", "").strip()
            raw_snippet = item.get("snippet", "")
            # Strip Wikipedia highlighting HTML spans
            clean_snippet = re.sub(r"<[^>]+>", "", raw_snippet).strip()
            if title:
                encoded_title = quote(title.replace(" ", "_"))
                article_url = f"https://en.wikipedia.org/wiki/{encoded_title}"
                results.append(
                    ToolResult(
                        content=clean_snippet or title,
                        source_title=f"{title} - Wikipedia",
                        source_url=article_url,
                        metadata={"pageid": item.get("pageid")},
                    )
                )

        return results
