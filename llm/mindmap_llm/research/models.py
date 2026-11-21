"""
Data models and typed states for the bounded multi-step research agent subgraph (S3-A2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict
from uuid import UUID

from contracts.models import ChunkSource


@dataclass
class ResearchSource:
    """Normalized research item retrieved by any tool (Tavily, Wikipedia, Scholar, etc.).

    Preserves rich metadata (DOI, canonical URL, snippet, author, published date)
    and enables precise deduplication across search providers.
    """

    content: str
    source_title: str | None
    source_url: str
    tool: str  # "tavily" | "wikipedia" | "scholar" | "arxiv"
    doi: str | None = None
    canonical_url: str = ""
    canonical_doi: str | None = None
    authors: list[str] = field(default_factory=list)
    published_date: str | None = None
    chunk_source: ChunkSource = ChunkSource.WEB_SEARCH
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Deterministic dictionary representation for Redis serialization and logging."""
        return {
            "content": self.content,
            "source_title": self.source_title,
            "source_url": self.source_url,
            "tool": self.tool,
            "doi": self.doi,
            "canonical_url": self.canonical_url,
            "canonical_doi": self.canonical_doi,
            "authors": self.authors,
            "published_date": self.published_date,
            "chunk_source": str(self.chunk_source.value if hasattr(self.chunk_source, "value") else self.chunk_source),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchSource:
        """Reconstruct ResearchSource from serialized dictionary."""
        src_val = data.get("chunk_source", "web_search")
        try:
            chunk_src = ChunkSource(src_val)
        except (ValueError, KeyError):
            chunk_src = ChunkSource.WEB_SEARCH

        return cls(
            content=data.get("content", ""),
            source_title=data.get("source_title"),
            source_url=data.get("source_url", ""),
            tool=data.get("tool", "unknown"),
            doi=data.get("doi"),
            canonical_url=data.get("canonical_url", ""),
            canonical_doi=data.get("canonical_doi"),
            authors=data.get("authors", []),
            published_date=data.get("published_date"),
            chunk_source=chunk_src,
            metadata=data.get("metadata", {}),
        )


class ResearchState(TypedDict, total=False):
    """LangGraph state passed between nodes in the research subgraph.

    Includes round tracking (max 2 rounds), planned queries, raw & deduplicated
    sources, sufficiency flags, error warnings, and degraded-run metadata.
    """

    # --- Inputs ---
    seed_text: str
    namespace: str
    job_id: UUID
    user_id: UUID | str | None
    active_tools: list[str]
    max_rounds: int
    current_round: int

    # --- Working State ---
    queries_by_tool: dict[str, list[str]]
    executed_queries: list[str]
    raw_sources: list[ResearchSource]
    deduped_sources: list[ResearchSource]
    is_sufficient: bool
    sufficiency_reason: str

    # --- Degradation & Diagnostics ---
    degraded: bool
    warnings: list[str]
    cache_hits: int
    tool_failures: list[str]
    timed_out: bool
