"""
Research agent package (S3-A2).
Provides a bounded multi-step research agent subgraph with query planning,
parallel multi-source dispatch (Tavily, Wikipedia, Scholar), source deduplication,
Redis caching, sufficiency evaluation, global timeout enforcement, and graceful degradation.
"""

from __future__ import annotations

from research.models import ResearchSource, ResearchState

__all__ = ["ResearchSource", "ResearchState"]
