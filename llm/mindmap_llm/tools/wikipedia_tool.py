"""
Wikipedia REST research tool (S3-T1, fixes #184).

Queries the Wikipedia REST API for topic seeds:

  - GET /api/rest_v1/page/summary/{title}        -> article summary + canonical URL
  - GET /api/rest_v1/page/mobile-sections/{title} -> section tree

The Mobile Content Service backing `/page/mobile-sections` is decommissioned,
so when it fails the tool degrades gracefully to the MediaWiki Action API
`action=parse&prop=sections`, which returns the same section hierarchy.

Wikipedia text is available under CC BY-SA; every returned chunk carries the
canonical article URL plus CC BY-SA attribution metadata so the frontend can
surface clean source attribution (consumed by S3-FE3).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
from providers.base import ToolSpec
from tools.base import Tool, ToolError, ToolResult

# The Wikimedia REST API requires a descriptive User-Agent on all requests.
WIKIPEDIA_USER_AGENT = (
    "MindMapGenerator/1.0 (https://github.com/Saumya2721/IT314_Group15_MindMap-Generator; "
    "contact@mindmap-generator.example)"
)

WIKIPEDIA_LICENSE = "CC BY-SA 4.0"

WIKIPEDIA_ATTRIBUTION_TEMPLATE = (
    "Content from Wikipedia article '{title}' is available under the Creative "
    "Commons Attribution-ShareAlike License (CC BY-SA 4.0). Source: {url}"
)

# MediaWiki Action API fallback endpoint used when the decommissioned
# /page/mobile-sections REST endpoint cannot be reached.
WIKIPEDIA_API_FALLBACK_BASE = "https://en.wikipedia.org/w/api.php"


class WikipediaTool(Tool):
    """Research tool that returns Wikipedia article summaries with section trees."""

    def __init__(
        self,
        api_base: str = "https://en.wikipedia.org/api/rest_v1",
        timeout_s: float = 10.0,
        max_sections: int = 30,
    ):
        self._api_base = api_base.rstrip("/")
        self._timeout_s = timeout_s
        self._max_sections = max_sections

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="wikipedia",
            description="Look up a Wikipedia article for a topic: returns its lead summary, canonical URL, CC BY-SA attribution, and section hierarchy for outline generation.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Wikipedia article title or topic seed to look up.",
                    }
                },
                "required": ["query"],
            },
        )

    async def invoke(self, *, query: str = "", **kwargs: Any) -> list[ToolResult]:
        topic = (query or str(kwargs.get("query", "")) or "").strip()
        if not topic:
            raise ToolError("Wikipedia tool requires a non-empty query/topic.")
        slug = quote(topic, safe="")
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s, follow_redirects=True) as client:
                summary = await self._fetch_summary(client, slug)
                try:
                    sections = await self._fetch_sections(client, slug)
                except httpx.HTTPError:
                    # Section hierarchy is optional enrichment; never let a
                    # section-endpoint outage prevent returning the summary.
                    sections = []
        except httpx.HTTPError as exc:
            raise ToolError(f"Wikipedia lookup failed for topic {topic!r}: {exc}") from exc

        title = summary.get("title") or topic
        extract = summary.get("extract") or summary.get("description") or ""
        canonical_url = (
            summary.get("content_urls", {}).get("desktop", {}).get("page")
            or summary.get("content_urls", {}).get("mobile", {}).get("page")
            or summary.get("url")
            or ""
        )
        license_name = summary.get("license") or WIKIPEDIA_LICENSE
        attribution = WIKIPEDIA_ATTRIBUTION_TEMPLATE.format(title=title, url=canonical_url)

        result = ToolResult(
            content=extract,
            source_title=title,
            source_url=canonical_url,
            sections=sections or None,
            license=license_name,
            attribution=attribution,
        )
        return [result] if result.content.strip() or result.sections else []

    # ------------------------------------------------------------------
    # Wikipedia REST API calls
    # ------------------------------------------------------------------

    async def _fetch_summary(self, client: httpx.AsyncClient, slug: str) -> dict[str, Any]:
        resp = await client.get(f"{self._api_base}/page/summary/{slug}", headers=self._headers())
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ToolError("Wikipedia summary response was not a JSON object.")
        return data

    async def _fetch_sections(self, client: httpx.AsyncClient, slug: str) -> list[str]:
        """Return top-level section titles for the article, preserving order."""
        sections: list[str] = []
        try:
            resp = await client.get(f"{self._api_base}/page/mobile-sections/{slug}", headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                sections.extend(self._titles_from_mobile_sections(data.get("sections")))
        except httpx.HTTPError:
            # The Mobile Content Service behind /page/mobile-sections is
            # decommissioned; fall back to the MediaWiki Action API so section
            # hierarchy is still available when possible.
            sections.extend(await self._fetch_sections_via_action_api(client, slug))

        top_level = [t for t in sections if t.strip()]
        return top_level[: self._max_sections]

    async def _fetch_sections_via_action_api(self, client: httpx.AsyncClient, slug: str) -> list[str]:
        params = {
            "action": "parse",
            "page": slug.replace("%5F", "_"),  # MediaWiki action API accepts the encoded slug
            "prop": "sections",
            "format": "json",
            "formatversion": "2",
            "origin": "*",
        }
        resp = await client.get(WIKIPEDIA_API_FALLBACK_BASE, params=params, headers=self._headers())
        resp.raise_for_status()
        data = resp.json()
        sections = []
        if isinstance(data, dict):
            parse = data.get("parse") or {}
            raw_sections = parse.get("sections") or []
            for section in raw_sections:
                title = section.get("title") or section.get("line") or ""
                if title:
                    sections.append(title)
        return sections

    @staticmethod
    def _titles_from_mobile_sections(raw_sections: Any) -> list[str]:
        """Flatten mobile-sections JSON into ordered section titles.

        Sections are nested (parents listed before children); we keep every
        title in document order and let callers truncate to the top levels.
        """
        titles: list[str] = []
        if not isinstance(raw_sections, list):
            return titles

        def visit(node: dict[str, Any]) -> None:
            title = node.get("line") or node.get("title") or node.get("text") or ""
            if title:
                titles.append(title)
            children = node.get("children")
            if isinstance(children, list):
                for child in children:
                    visit(child)

        for node in raw_sections:
            visit(node)
        return titles

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": WIKIPEDIA_USER_AGENT, "Accept": "application/json"}
