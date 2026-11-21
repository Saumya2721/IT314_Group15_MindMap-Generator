"""
Scholar / academic paper search tool implementation (FR-29, S3-A2).
Queries academic paper APIs (Semantic Scholar / Crossref) for scholarly literature, DOIs, and abstracts.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
from providers.base import ToolSpec
from tools.base import Tool, ToolError, ToolResult


class ScholarTool(Tool):
    """Searches academic repositories for peer-reviewed literature, abstracts, and DOIs."""

    def __init__(
        self,
        api_base: str = "https://api.semanticscholar.org/graph/v1/paper/search",
        api_key: str = "",
        timeout_s: float = 15.0,
        max_results: int = 4,
    ):
        self._api_base = api_base
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._max_results = max_results

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="scholar",
            description="Search scholarly and academic literature for research papers, methodologies, and empirical studies.",
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
            "query": query_val,
            "limit": self._max_results,
            "fields": "title,abstract,authors,year,externalIds,url",
        }

        headers: dict[str, str] = {"User-Agent": "MindMapGenerator/1.0 (academic software engineering project)"}
        if self._api_key:
            headers["x-api-key"] = self._api_key

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s, follow_redirects=True) as client:
                resp = await client.get(self._api_base, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise ToolError(f"Scholar search failed: {exc}") from exc
        except Exception as exc:
            raise ToolError(f"Scholar search error: {exc}") from exc

        results: list[ToolResult] = []
        papers = data.get("data", [])
        for paper in papers:
            title = (paper.get("title") or "").strip()
            abstract = (paper.get("abstract") or "").strip()
            external_ids = paper.get("externalIds") or {}
            doi = external_ids.get("DOI") or external_ids.get("doi")
            url = paper.get("url")
            if not url and doi:
                url = f"https://doi.org/{doi}"
            elif not url and title:
                url = f"https://scholar.google.com/scholar?q={quote(title)}"

            author_names = [a.get("name", "") for a in paper.get("authors", []) if a.get("name")]
            year = paper.get("year")

            if title and url:
                results.append(
                    ToolResult(
                        content=abstract or title,
                        source_title=title,
                        source_url=url,
                        doi=doi,
                        metadata={"authors": author_names, "year": year},
                    )
                )

        return results
