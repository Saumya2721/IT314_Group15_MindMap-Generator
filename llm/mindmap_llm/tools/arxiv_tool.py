from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx
from providers.base import ToolSpec
from tools.base import Tool, ToolError, ToolResult

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


class ArxivTool(Tool):
    def __init__(self, api_base: str, timeout_s: float = 15.0, max_results: int = 5):
        self._api_base = api_base
        self._timeout_s = timeout_s
        self._max_results = max_results

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="arxiv_search",
            description=(
                "Search arXiv for papers relevant to a topic. Use for academic/technical grounding."
            ),
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )

    async def invoke(self, *, query: str) -> list[ToolResult]:
        params: dict[str, str | int] = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": self._max_results,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.get(self._api_base, params=params)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ToolError(f"arXiv search failed: {exc}") from exc

        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            raise ToolError(f"arXiv returned unparseable XML: {exc}") from exc

        results: list[ToolResult] = []
        for entry in root.findall("atom:entry", ATOM_NS):
            title = (entry.findtext("atom:title", default="", namespaces=ATOM_NS) or "").strip()
            summary = (entry.findtext("atom:summary", default="", namespaces=ATOM_NS) or "").strip()
            link = entry.findtext("atom:id", default="", namespaces=ATOM_NS) or ""
            if title and link:
                results.append(ToolResult(content=summary, source_title=title, source_url=link))
        return results
