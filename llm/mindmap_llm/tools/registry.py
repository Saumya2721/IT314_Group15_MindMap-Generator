"""
Tool registry. Adding a new research tool for Developers (FR-29, US-12):
  1. implement tools.base.Tool in a new file,
  2. instantiate and add it to `build_tool_registry()` below.
Nothing in orchestration/ changes.
"""

from __future__ import annotations

from config import Settings
from tools.arxiv_tool import ArxivTool
from tools.base import Tool
from tools.policy import ToolQuotaLimiter, wrap_tool_registry
from tools.web_search_tool import TavilyWebSearchTool
from tools.wikipedia_tool import WikipediaTool


def build_tool_registry(
    settings: Settings,
    limiter: ToolQuotaLimiter | None = None,
) -> dict[str, Tool]:
    registry: dict[str, Tool] = {
        "arxiv_search": ArxivTool(
            api_base=settings.tools.arxiv_api_base,
            timeout_s=settings.tools.arxiv_search_timeout_s,
        ),
    }
    if settings.tools.web_search_api_key:
        try:
            registry["web_search"] = TavilyWebSearchTool(
                api_key=settings.tools.web_search_api_key,
                timeout_s=settings.tools.web_search_timeout_s,
            )
        except RuntimeError:
            pass  # no key configured — web_search simply won't be offered to the LLM

    # S3-T1 / fixes #184: Wikipedia research tool (CC BY-SA attribution, section hierarchy)
    if settings.tools.wikipedia_enabled:
        registry["wikipedia"] = WikipediaTool(
            api_base=settings.tools.wikipedia_api_base,
            timeout_s=settings.tools.wikipedia_timeout_s,
            max_sections=settings.tools.wikipedia_max_sections,
        )

    # S3-L1: Apply policy enforcement layer (per-tool timeouts, enable/disable flags, quota limits)
    return wrap_tool_registry(registry, settings, limiter=limiter)
