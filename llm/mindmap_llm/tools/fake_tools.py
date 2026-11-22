"""Deterministic fake tools for CI tests and offline generation (S3-L5).

Provides in-memory, deterministic implementations of the five research tools:
- FakeArxivTool: academic paper search returning deterministic arXiv citations.
- FakeScholarTool: scholarly literature search returning DOIs and citation metadata.
- FakeWebSearchTool: general web search returning deterministic search snippets.
- FakeWebPageReaderTool: SSRF-guarded web reader returning deterministic Markdown.
- FakeWikipediaTool: Wikipedia lookup returning lead summaries, section trees, and CC BY-SA attribution.

None of these tools perform external HTTP, DNS, or socket operations.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from config import Settings
from providers.base import ToolSpec
from tools.base import Tool, ToolError, ToolResult
from tools.policy import ToolQuotaLimiter


class FakeArxivTool(Tool):
    """Deterministic fake arXiv search tool for testing without network calls."""

    def __init__(self, custom_results: list[ToolResult] | None = None):
        self._custom_results = custom_results
        self.calls: list[dict[str, Any]] = []

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="arxiv_search",
            description="Search arXiv for papers relevant to a topic. Use for academic/technical grounding.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )

    def set_custom_results(self, results: list[ToolResult] | None) -> None:
        self._custom_results = results

    async def invoke(self, *, query: str = "", **kwargs: Any) -> list[ToolResult]:
        call_args = {"query": query, **kwargs}
        self.calls.append(call_args)
        if self._custom_results is not None:
            return list(self._custom_results)

        clean_query = (query or str(kwargs.get("query", ""))).strip()
        if not clean_query:
            return []

        paper_id = f"2401.{abs(hash(clean_query)) % 90000 + 10000:05d}"
        url = f"https://arxiv.org/abs/{paper_id}"
        title = f"Advances in {clean_query.title()}: A Comprehensive Survey"
        content = (
            f"This paper presents a formal analysis and empirical evaluation of {clean_query}. "
            f"We introduce state-of-the-art methodology, theoretical proofs, and benchmark results."
        )
        return [
            ToolResult(
                content=content,
                source_title=title,
                source_url=url,
                metadata={
                    "authors": ["A. Deterministic", "B. Researcher"],
                    "year": 2024,
                    "primary_category": "cs.AI",
                    "arxiv_id": paper_id,
                },
            )
        ]


class FakeScholarTool(Tool):
    """Deterministic fake Semantic Scholar search tool for testing without network calls."""

    def __init__(self, custom_results: list[ToolResult] | None = None):
        self._custom_results = custom_results
        self.calls: list[dict[str, Any]] = []

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

    def set_custom_results(self, results: list[ToolResult] | None) -> None:
        self._custom_results = results

    async def invoke(self, *, query: str = "", **kwargs: Any) -> list[ToolResult]:
        call_args = {"query": query, **kwargs}
        self.calls.append(call_args)
        if self._custom_results is not None:
            return list(self._custom_results)

        clean_query = (query or str(kwargs.get("query", ""))).strip()
        if not clean_query:
            return []

        paper_hash = f"{abs(hash(clean_query)):040x}"[:40]
        url = f"https://www.semanticscholar.org/paper/{paper_hash}"
        doi = f"10.1000/mock.doi.{abs(hash(clean_query)) % 100000:05d}"
        title = f"Empirical Foundations of {clean_query.title()}"
        content = (
            f"Abstract: We investigate the core mechanisms and experimental performance of {clean_query}. "
            f"Results demonstrate statistical significance across established benchmarks."
        )
        return [
            ToolResult(
                content=content,
                source_title=title,
                source_url=url,
                doi=doi,
                metadata={
                    "authors": ["C. Scholar", "D. Scientist"],
                    "year": 2023,
                    "citation_count": 88,
                    "paper_id": paper_hash,
                },
            )
        ]


class FakeWebSearchTool(Tool):
    """Deterministic fake web search tool (Tavily stand-in) without network calls."""

    def __init__(self, custom_results: list[ToolResult] | None = None):
        self._custom_results = custom_results
        self.calls: list[dict[str, Any]] = []

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

    def set_custom_results(self, results: list[ToolResult] | None) -> None:
        self._custom_results = results

    async def invoke(self, *, query: str = "", **kwargs: Any) -> list[ToolResult]:
        call_args = {"query": query, **kwargs}
        self.calls.append(call_args)
        if self._custom_results is not None:
            return list(self._custom_results)

        clean_query = (query or str(kwargs.get("query", ""))).strip()
        if not clean_query:
            return []

        slug = re.sub(r"[^a-zA-Z0-9]+", "-", clean_query.lower()).strip("-") or "topic"
        url = f"https://example.com/search/{slug}"
        title = f"Overview and Guide to {clean_query.title()}"
        content = (
            f"A comprehensive overview covering definitions, key concepts, and practical insights "
            f"regarding {clean_query}."
        )
        return [
            ToolResult(
                content=content,
                source_title=title,
                source_url=url,
                metadata={"snippet": content},
            )
        ]


class FakeWebPageReaderTool(Tool):
    """Deterministic fake web page reader with basic URL scheme validation without network calls."""

    def __init__(self, custom_results: list[ToolResult] | None = None):
        self._custom_results = custom_results
        self.calls: list[dict[str, Any]] = []

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="web_page_reader",
            description="Fetch and read the full text content of a web page URL safely converted to clean Markdown with SSRF guards.",
            input_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The HTTP or HTTPS URL of the web page to fetch.",
                    }
                },
                "required": ["url"],
            },
        )

    def set_custom_results(self, results: list[ToolResult] | None) -> None:
        self._custom_results = results

    async def invoke(self, *, url: str = "", **kwargs: Any) -> list[ToolResult]:
        call_args = {"url": url, **kwargs}
        self.calls.append(call_args)
        target_url = (url or str(kwargs.get("url", ""))).strip()
        if not target_url:
            raise ToolError("URL argument is required for web_page_reader.")

        if self._custom_results is not None:
            return list(self._custom_results)

        parsed = urllib.parse.urlparse(target_url)
        if parsed.scheme.lower() not in ("http", "https"):
            raise ToolError(f"Unsupported URL scheme '{parsed.scheme}': only http and https are permitted.")

        title = f"Document at {parsed.netloc or 'example.com'}"
        content = (
            f"# {title}\n\n"
            f"## Executive Summary\n"
            f"Deterministic extracted Markdown content for {target_url}.\n\n"
            f"## Key Takeaways\n"
            f"- Primary conceptual insight for {target_url}\n"
            f"- Structured context and supporting points\n"
        )
        return [
            ToolResult(
                content=content,
                source_title=title,
                source_url=target_url,
                metadata={"status_code": 200, "content_type": "text/html"},
            )
        ]


class FakeWikipediaTool(Tool):
    """Deterministic fake Wikipedia tool returning summaries, section trees, and CC BY-SA attribution."""

    def __init__(self, custom_results: list[ToolResult] | None = None):
        self._custom_results = custom_results
        self.calls: list[dict[str, Any]] = []

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

    def set_custom_results(self, results: list[ToolResult] | None) -> None:
        self._custom_results = results

    async def invoke(self, *, query: str = "", **kwargs: Any) -> list[ToolResult]:
        call_args = {"query": query, **kwargs}
        self.calls.append(call_args)
        if self._custom_results is not None:
            return list(self._custom_results)

        clean_query = (
            (query or "").strip() or str(kwargs.get("query", "")).strip() or str(kwargs.get("title", "")).strip()
        )
        if not clean_query:
            return []

        clean_title = clean_query.title()
        title_slug = clean_title.replace(" ", "_")
        canonical_url = f"https://en.wikipedia.org/wiki/{title_slug}"
        content = (
            f"{clean_title} is a notable topic of general and academic interest. "
            f"This deterministic article summary provides foundational definitions, background, "
            f"and historical context suitable for outline generation."
        )
        sections = [
            "Introduction",
            "History and Background",
            "Core Principles",
            "Applications and Implications",
            "See also",
        ]
        license_str = "CC BY-SA 4.0"
        attribution = (
            f"Content from Wikipedia article '{clean_title}' is available under the "
            f"Creative Commons Attribution-ShareAlike License ({license_str}). Source: {canonical_url}"
        )

        return [
            ToolResult(
                content=content,
                source_title=clean_title,
                source_url=canonical_url,
                sections=list(sections),
                license=license_str,
                attribution=attribution,
                metadata={"canonical_url": canonical_url, "sections": list(sections)},
            )
        ]


def build_fake_tool_registry(
    settings: Settings | None = None,
    limiter: ToolQuotaLimiter | None = None,
    *,
    wrap_policy: bool = False,
) -> dict[str, Tool]:
    """Build a centralized registry containing all five fake research tools.

    Keys match the production registry:
      - 'arxiv_search': FakeArxivTool
      - 'scholar': FakeScholarTool
      - 'web_search': FakeWebSearchTool
      - 'web_page_reader': FakeWebPageReaderTool
      - 'wikipedia': FakeWikipediaTool
    """
    registry: dict[str, Tool] = {
        "arxiv_search": FakeArxivTool(),
        "scholar": FakeScholarTool(),
        "web_search": FakeWebSearchTool(),
        "web_page_reader": FakeWebPageReaderTool(),
        "wikipedia": FakeWikipediaTool(),
    }
    if wrap_policy and settings is not None:
        from tools.policy import wrap_tool_registry

        return wrap_tool_registry(registry, settings, limiter=limiter)
    return registry
