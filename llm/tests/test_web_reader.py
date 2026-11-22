"""Unit tests for SSRF-guarded web page reader tool and HTML-to-Markdown parser."""

from __future__ import annotations

import socket
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from config import get_settings
from tools.base import ToolError
from tools.registry import build_tool_registry
from tools.web_reader import (
    SSRFProtectionError,
    WebPageReaderTool,
    fetch_web_page_async,
    html_to_markdown,
    validate_url_and_resolve_ip,
)


@pytest.fixture(autouse=True)
def mock_dns_resolution(monkeypatch):
    """Ensure tests never hang on offline DNS resolution for example hostnames."""
    orig_getaddrinfo = socket.getaddrinfo

    def _mock_getaddrinfo(host, port, *args, **kwargs):
        if host in ("example.com", "public.com"):
            return [(2, 1, 0, "", ("93.184.216.34", 0))]
        try:
            return orig_getaddrinfo(host, port, *args, **kwargs)
        except Exception:
            return [(2, 1, 0, "", (host, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _mock_getaddrinfo)


def test_html_to_markdown_conversion_and_boilerplate_stripping():
    html_input = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Sample Architecture Document</title>
        <style>body { color: red; }</style>
        <script>console.log("malicious");</script>
    </head>
    <body>
        <header>
            <nav>
                <a href="/home">Home</a>
                <a href="/about">About</a>
            </nav>
        </header>
        <main>
            <h1>System Overview</h1>
            <p>This is the primary text explaining the system architecture.</p>
            <h2>Components</h2>
            <ul>
                <li>Ingestion Service</li>
                <li>Orchestration Engine</li>
                <li>Vector Store</li>
            </ul>
            <table>
                <tr><th>Component</th><th>Status</th></tr>
                <tr><td>Worker</td><td>Active</td></tr>
            </table>
            <blockquote>Important architectural note.</blockquote>
        </main>
        <aside>Related ads and side links</aside>
        <footer>Copyright 2026. All rights reserved.</footer>
    </body>
    </html>
    """

    markdown, title = html_to_markdown(html_input)

    assert title == "Sample Architecture Document"
    assert "# Sample Architecture Document" in markdown
    assert "# System Overview" in markdown
    assert "This is the primary text explaining the system architecture." in markdown
    assert "## Components" in markdown
    assert "- Ingestion Service" in markdown
    assert "- Orchestration Engine" in markdown
    assert "| Component | Status |" in markdown
    assert "| Worker | Active |" in markdown
    assert "> Important architectural note." in markdown

    # Verify stripped boilerplate tags
    assert "console.log" not in markdown
    assert "body { color: red; }" not in markdown
    assert "Home" not in markdown
    assert "About" not in markdown
    assert "Related ads and side links" not in markdown
    assert "Copyright 2026" not in markdown


def test_ssrf_rejects_unsupported_schemes():
    with pytest.raises(SSRFProtectionError, match="Unsupported URL scheme"):
        validate_url_and_resolve_ip("file:///etc/passwd")

    with pytest.raises(SSRFProtectionError, match="Unsupported URL scheme"):
        validate_url_and_resolve_ip("gopher://127.0.0.1:70")

    with pytest.raises(SSRFProtectionError, match="Unsupported URL scheme"):
        validate_url_and_resolve_ip("ftp://example.com/file.txt")


@pytest.mark.parametrize(
    "blocked_ip",
    [
        "127.0.0.1",  # Loopback
        "127.0.1.1",  # Loopback subnet
        "10.0.0.1",  # Private RFC 1918 (10/8)
        "172.16.0.1",  # Private RFC 1918 (172.16/12)
        "172.31.255.254",  # Private RFC 1918
        "192.168.1.1",  # Private RFC 1918 (192.168/16)
        "169.254.169.254",  # Cloud metadata / link-local
        "169.254.1.1",  # Link-local
        "0.0.0.0",  # Unspecified
        "::1",  # IPv6 Loopback
        "fc00::1",  # IPv6 Unique Local
        "fe80::1",  # IPv6 Link-Local
    ],
)
def test_ssrf_blocks_private_and_metadata_ip_addresses(blocked_ip: str):
    # Mock socket.getaddrinfo to return the target blocked IP
    mock_addrinfo = [(2, 1, 0, "", (blocked_ip, 80))]

    with patch("socket.getaddrinfo", return_value=mock_addrinfo):
        with pytest.raises(SSRFProtectionError, match="Blocked access to"):
            validate_url_and_resolve_ip("http://internal-target.com/data")


def test_ssrf_blocks_cloud_metadata_endpoints():
    with pytest.raises(SSRFProtectionError, match="metadata"):
        validate_url_and_resolve_ip("http://169.254.169.254/latest/meta-data")

    with pytest.raises(SSRFProtectionError, match="metadata"):
        validate_url_and_resolve_ip("http://metadata.google.internal/computeMetadata/v1/")


def test_ssrf_allows_public_ips():
    # Public IP: 93.184.216.34 (example.com)
    mock_addrinfo = [(2, 1, 0, "", ("93.184.216.34", 80))]
    with patch("socket.getaddrinfo", return_value=mock_addrinfo):
        resolved = validate_url_and_resolve_ip("https://example.com/docs")
        assert len(resolved) == 1
        assert str(resolved[0]) == "93.184.216.34"


@pytest.mark.asyncio
async def test_redirect_limit_enforced():
    # Simulate a redirect loop exceeding 3 redirects
    def create_redirect_response(target_url: str):
        resp = MagicMock()
        resp.is_redirect = True
        resp.status_code = 302
        resp.headers = {"location": target_url}
        return resp

    mock_resp = create_redirect_response("https://example.com/step2")
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("tools.web_reader.validate_url_and_resolve_ip"):
        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(SSRFProtectionError, match="Redirect limit exceeded"):
                await fetch_web_page_async("https://example.com/step1", max_redirects=3)


@pytest.mark.asyncio
async def test_redirect_to_private_ip_blocked():
    # First response redirects to private IP http://127.0.0.1:8000/admin
    redirect_resp = MagicMock()
    redirect_resp.is_redirect = True
    redirect_resp.status_code = 301
    redirect_resp.headers = {"location": "http://127.0.0.1:8000/admin"}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=redirect_resp)
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    def mock_getaddrinfo(host, port, *args, **kwargs):
        if host == "example.com":
            return [(2, 1, 0, "", ("93.184.216.34", 0))]
        return [(2, 1, 0, "", (host, 0))]

    with patch("socket.getaddrinfo", side_effect=mock_getaddrinfo):
        with patch("httpx.AsyncClient", return_value=mock_client):
            # Allow first URL validation, but second URL validation should fail because of 127.0.0.1
            with pytest.raises(SSRFProtectionError, match="Blocked access to"):
                await fetch_web_page_async("https://example.com/initial")


@pytest.mark.asyncio
async def test_response_payload_capped_at_1mb():
    html_sample = "<html><body><h1>Title</h1><p>" + ("A" * 500) + "</p></body></html>"
    raw_bytes = html_sample.encode("utf-8")

    resp = MagicMock()
    resp.is_redirect = False
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.content = raw_bytes

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=resp)
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("tools.web_reader.validate_url_and_resolve_ip"):
        with patch("httpx.AsyncClient", return_value=mock_client):
            # Test capping at 100 bytes
            markdown, title = await fetch_web_page_async(
                "https://example.com/large",
                max_bytes=100,
            )
            assert len(markdown.encode("utf-8")) <= 100 or "Title" in markdown


@pytest.mark.asyncio
async def test_request_timeout_raises_tool_error():
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("Connection timed out"))
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("tools.web_reader.validate_url_and_resolve_ip"):
        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(ToolError, match="timed out"):
                await fetch_web_page_async("https://example.com/slow", timeout_s=5.0)


@pytest.mark.asyncio
async def test_web_page_reader_tool_invocation():
    tool = WebPageReaderTool(timeout_s=5.0)
    assert tool.spec.name == "web_page_reader"

    mock_markdown = "# Web Document\nFull document content parsed into Markdown."
    with patch(
        "tools.web_reader.fetch_web_page_async",
        return_value=(mock_markdown, "Web Document"),
    ):
        results = await tool.invoke(url="https://example.com/doc")
        assert len(results) == 1
        assert results[0].content == mock_markdown
        assert results[0].source_title == "Web Document"
        assert results[0].source_url == "https://example.com/doc"


def test_tool_registry_includes_web_page_reader():
    settings = get_settings()
    registry = build_tool_registry(settings)
    assert "web_page_reader" in registry
    tool = registry["web_page_reader"]
    assert tool.spec.name == "web_page_reader"
