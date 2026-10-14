"""
Tool registry. Adding a new research tool for Developers (FR-29, US-12):
  1. implement tools.base.Tool in a new file,
  2. instantiate and add it to `build_tool_registry()` below.
Nothing in orchestration/ changes.
"""

from __future__ import annotations

from config import LLMSettings
from tools.arxiv_tool import ArxivTool
from tools.base import Tool
from tools.web_search_tool import TavilyWebSearchTool


def build_tool_registry(settings: LLMSettings) -> dict[str, Tool]:
    registry: dict[str, Tool] = {
        "arxiv_search": ArxivTool(
            api_base=settings.tools.arxiv_api_base, timeout_s=settings.tools.tool_timeout_s
        ),
    }
    if settings.tools.web_search_api_key:
        try:
            registry["web_search"] = TavilyWebSearchTool(
                api_key=settings.tools.web_search_api_key, timeout_s=settings.tools.tool_timeout_s
            )
        except RuntimeError:
            pass  # no key configured — web_search simply won't be offered to the LLM
    return registry
