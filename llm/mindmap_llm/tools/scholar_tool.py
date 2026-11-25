"""
Scholar / academic paper search tool implementation (FR-29, S3-A2).
Queries the Semantic Scholar Academic Graph for papers and citation metadata.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
from providers.base import ToolSpec
from tools.base import Tool, ToolError, ToolResult
from tracing import trace_span


class ScholarTool(Tool):
    """Searches academic repositories for peer-reviewed literature, abstracts, and DOIs."""

    def __init__(
        self,
        api_base: str = "https://api.semanticscholar.org/graph/v1/paper/search",
        api_key: str = "",
        timeout_s: float = 15.0,
        max_results: int = 4,
    ):
        endpoint = api_base.rstrip("/")
        self._api_base = f"{endpoint}/paper/search" if endpoint.endswith("/graph/v1") else endpoint
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

    @trace_span("scholar_invoke", run_type="tool")
    async def invoke(self, *, query: str = "", **kwargs: Any) -> list[ToolResult]:
        query_val = query.strip()
        if not query_val:
            return []

        params: dict[str, str | int] = {
            "query": query_val,
            "limit": self._max_results,
            "fields": "title,abstract,authors,year,citationCount,externalIds,url",
        }

        headers: dict[str, str] = {"User-Agent": "MindMapGenerator/1.0 (academic software engineering project)"}
        if self._api_key:
            headers["x-api-key"] = self._api_key

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.get(self._api_base, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise ToolError(f"Scholar search failed: {exc}") from exc
        except ValueError as exc:
            raise ToolError(f"Scholar search error: {exc}") from exc

        results: list[ToolResult] = []
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise ToolError("Scholar search returned an invalid response")
        for paper in data["data"][: self._max_results]:
            if not isinstance(paper, dict) or not isinstance(paper.get("title"), str):
                continue
            title = paper["title"].strip()
            if not title:
                continue
            raw_abstract = paper.get("abstract")
            abstract = raw_abstract.strip() if isinstance(raw_abstract, str) else ""
            # Pinecone has a per-record metadata size limit.
            abstract = abstract[:6000]
            external_ids = paper.get("externalIds") or {}
            raw_doi = (external_ids.get("DOI") or external_ids.get("doi")) if isinstance(external_ids, dict) else None
            doi = raw_doi.strip() if isinstance(raw_doi, str) and raw_doi.strip() else None
            doi_url = f"https://doi.org/{quote(doi, safe='/')}" if doi else None
            authors = paper.get("authors")
            author_names = (
                [author["name"] for author in authors if isinstance(author, dict) and author.get("name")]
                if isinstance(authors, list)
                else []
            )
            raw_year = paper.get("year")
            year = raw_year if type(raw_year) is int else None
            citation_count = paper.get("citationCount")
            raw_url = paper.get("url")
            url = raw_url.strip() if isinstance(raw_url, str) else ""
            if not url.startswith(("https://", "http://")):
                url = doi_url or f"https://www.semanticscholar.org/search?q={quote(title)}"
            results.append(
                ToolResult(
                    content=abstract or title,
                    source_title=title,
                    source_url=url,
                    doi=doi,
                    attribution="Semantic Scholar",
                    metadata={
                        "authors": author_names,
                        "year": year,
                        "paper": {
                            "title": title,
                            "abstract": abstract or None,
                            "year": year,
                            "citation_count": citation_count
                            if type(citation_count) is int and citation_count >= 0
                            else None,
                            "doi_url": doi_url,
                        },
                    },
                )
            )

        return results
