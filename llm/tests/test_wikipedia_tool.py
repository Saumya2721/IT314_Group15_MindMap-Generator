"""
Unit tests for the Wikipedia research tool (S3-T1, fixes #184).

All Wikipedia REST responses are mocked via httpx; no real network calls are made.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from tools.base import ToolError
from tools.wikipedia_tool import WikipediaTool


def _make_response(json_payload: dict[str, Any], status_code: int = 200) -> AsyncMock:
    resp = AsyncMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_payload
    resp.raise_for_status.return_value = None
    return resp


def _make_httpx_client(summary: dict[str, Any], sections_payload: Any = None, summary_status: int = 200) -> AsyncMock:
    if sections_payload is None:
        sections_payload = []
    client = AsyncMock(spec=httpx.AsyncClient)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    responses = [_make_response(summary, summary_status), _make_response({"sections": sections_payload})]
    client.get = AsyncMock(side_effect=responses)
    return client


SUMMARY_PAYLOAD = {
    "title": "Machine learning",
    "pageid": 12345,
    "extract": "Machine learning is a field of study that gives computers the ability to learn without being explicitly programmed.",
    "content_urls": {
        "desktop": {"page": "https://en.wikipedia.org/wiki/Machine_learning"},
        "mobile": {"page": "https://en.m.wikipedia.org/wiki/Machine_learning"},
    },
    "license": "CC BY-SA 4.0",
}

SECTIONS_PAYLOAD = [
    {"line": "History"},
    {"line": "Approaches", "children": [{"line": "Supervised learning"}, {"line": "Unsupervised learning"}]},
    {"line": "See also"},
]


class TestWikipediaToolSuccess:
    @pytest.mark.asyncio
    async def test_returns_tool_result_with_summary_and_sections(self):
        tool = WikipediaTool(timeout_s=10.0)
        client = _make_httpx_client(SUMMARY_PAYLOAD, SECTIONS_PAYLOAD)
        with patch("httpx.AsyncClient", return_value=client):
            results = await tool.invoke(query="Machine learning")

        assert len(results) == 1
        result = results[0]
        assert result.source_title == "Machine learning"
        assert result.source_url == "https://en.wikipedia.org/wiki/Machine_learning"
        assert "Machine learning is a field of study" in result.content
        assert result.sections == ["History", "Approaches", "Supervised learning", "Unsupervised learning", "See also"]
        assert result.license == "CC BY-SA 4.0"
        assert result.attribution is not None
        assert "Machine learning" in result.attribution
        assert "CC BY-SA 4.0" in result.attribution
        assert "https://en.wikipedia.org/wiki/Machine_learning" in result.attribution
        # summary and sections endpoints called in order
        assert client.get.await_count == 2

    @pytest.mark.asyncio
    async def test_attribution_formats_title_and_url(self):
        tool = WikipediaTool()
        payload = {
            "title": "Artificial intelligence",
            "extract": "Evolving...",
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Artificial_intelligence"}},
        }
        client = _make_httpx_client(payload, None)
        with patch("httpx.AsyncClient", return_value=client):
            results = await tool.invoke(query="Artificial intelligence")
        assert len(results) == 1
        expected = (
            "Content from Wikipedia article 'Artificial intelligence' is available under the Creative "
            "Commons Attribution-ShareAlike License (CC BY-SA 4.0). Source: https://en.wikipedia.org/wiki/Artificial_intelligence"
        )
        assert results[0].attribution == expected

    @pytest.mark.asyncio
    async def test_uses_canonical_desktop_url(self):
        tool = WikipediaTool()
        payload = dict(SUMMARY_PAYLOAD)
        payload["content_urls"] = {"desktop": {"page": "https://en.wikipedia.org/wiki/Foo"}, "mobile": {}}
        client = _make_httpx_client(payload, None)
        with patch("httpx.AsyncClient", return_value=client):
            results = await tool.invoke(query="Foo")
        assert results[0].source_url == "https://en.wikipedia.org/wiki/Foo"


class TestWikipediaToolEdgeCases:
    @pytest.mark.asyncio
    async def test_mobile_sections_failure_falls_back_to_action_api(self):
        """The decommissioned /page/mobile-sections endpoint degrades gracefully."""
        tool = WikipediaTool()
        action_payload = {"parse": {"sections": [{"title": "History"}, {"title": "Contents"}]}}
        summary_resp = _make_response(SUMMARY_PAYLOAD)
        sections_resp = _make_response(action_payload)
        client = _make_httpx_client(SUMMARY_PAYLOAD, None)
        # first call: summary OK; second call (mobile-sections) raises; third call: action API OK
        client.get = AsyncMock(side_effect=[summary_resp, httpx.HTTPError("403 Forbidden"), sections_resp])
        with patch("httpx.AsyncClient", return_value=client):
            results = await tool.invoke(query="Machine learning")
        assert len(results) == 1
        assert results[0].sections == ["History", "Contents"]

    @pytest.mark.asyncio
    async def test_section_endpoints_outage_does_not_abort_summary(self):
        """If both section endpoints fail, the summary is still returned."""
        tool = WikipediaTool()
        client = _make_httpx_client(SUMMARY_PAYLOAD, None)
        # summary OK; mobile-sections and action-API fallback both raise
        client.get = AsyncMock(
            side_effect=[
                _make_response(SUMMARY_PAYLOAD),
                httpx.HTTPError("403 Forbidden"),
                httpx.HTTPError("500 Internal Server Error"),
            ]
        )
        with patch("httpx.AsyncClient", return_value=client):
            results = await tool.invoke(query="Machine learning")
        assert len(results) == 1
        assert results[0].content
        assert results[0].sections is None

    @pytest.mark.asyncio
    async def test_empty_query_raises_tool_error(self):
        tool = WikipediaTool()
        with pytest.raises(ToolError, match="non-empty"):
            await tool.invoke(query="")
        with pytest.raises(ToolError, match="non-empty"):
            await tool.invoke(query="   ")

    @pytest.mark.asyncio
    async def test_http_error_on_summary_raises_tool_error(self):
        tool = WikipediaTool()
        client = _make_httpx_client(SUMMARY_PAYLOAD, summary_status=404)
        client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError("404", request=AsyncMock(), response=AsyncMock(status_code=404))
        )
        with patch("httpx.AsyncClient", return_value=client):
            with pytest.raises(ToolError):
                await tool.invoke(query="NonexistentArticleXyz")

    @pytest.mark.asyncio
    async def test_no_content_and_no_sections_returns_empty(self):
        tool = WikipediaTool()
        empty_summary = {"title": "Empty", "extract": "", "content_urls": {}}
        client = _make_httpx_client(empty_summary, [])
        client.get = AsyncMock(side_effect=[_make_response(empty_summary), _make_response({"sections": []})])
        with patch("httpx.AsyncClient", return_value=client):
            results = await tool.invoke(query="Empty")
        assert results == []
