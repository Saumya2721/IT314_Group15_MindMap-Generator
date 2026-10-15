from __future__ import annotations

import httpx

from providers.base import ToolSpec
from tools.base import Tool, ToolError, ToolResult


class TavilyWebSearchTool(Tool):
    """Reference web-search implementation. Swap for another provider by
    implementing Tool and registering it in tools/registry.py instead —
    orchestration code depends only on the Tool interface."""

    def __init__(self, api_key: str, timeout_s: float = 15.0, max_results: int = 5):
        if not api_key:
            raise RuntimeError("SEARCH_API_KEY is not set")
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._max_results = max_results

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="web_search",
            description="Search the web for current, general information relevant to a topic.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )

    async def invoke(self, *, query: str) -> list[ToolResult]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={"api_key": self._api_key, "query": query, "max_results": self._max_results},
                )
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ToolError(f"Web search failed: {exc}") from exc

        data = resp.json()
        return [
            ToolResult(
                content=item.get("content", ""),
                source_title=item.get("title"),
                source_url=item["url"],
            )
            for item in data.get("results", [])
            if item.get("url")
        ]
