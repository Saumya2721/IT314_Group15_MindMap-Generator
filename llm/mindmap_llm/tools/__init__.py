"""Research tools and web reader modules."""

from tools.arxiv_tool import ArxivTool
from tools.base import Tool, ToolError, ToolResult
from tools.fake_tools import (
    FakeArxivTool,
    FakeScholarTool,
    FakeWebPageReaderTool,
    FakeWebSearchTool,
    FakeWikipediaTool,
    build_fake_tool_registry,
)
from tools.registry import build_tool_registry
from tools.web_reader import (
    SSRFProtectionError,
    WebPageReaderTool,
    fetch_web_page,
    fetch_web_page_async,
    html_to_markdown,
    validate_url_and_resolve_ip,
)
from tools.web_search_tool import TavilyWebSearchTool

__all__ = [
    "ArxivTool",
    "FakeArxivTool",
    "FakeScholarTool",
    "FakeWebPageReaderTool",
    "FakeWebSearchTool",
    "FakeWikipediaTool",
    "SSRFProtectionError",
    "TavilyWebSearchTool",
    "Tool",
    "ToolError",
    "ToolResult",
    "WebPageReaderTool",
    "build_fake_tool_registry",
    "build_tool_registry",
    "fetch_web_page",
    "fetch_web_page_async",
    "html_to_markdown",
    "validate_url_and_resolve_ip",
]
