"""Unit tests for SSRF-guarded web page reader tool and HTML-to-Markdown parser."""

from __future__ import annotations

import socket
from unittest.mock import patch

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


class _Body(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"<html><body>" + b"A" * 500 + b"</body></html>"


def _reader_transport(handler):
    original_client = httpx.AsyncClient
    return patch(
        "httpx.AsyncClient", side_effect=lambda **kw: original_client(transport=httpx.MockTransport(handler), **kw)
    )


@pytest.mark.asyncio
async def test_redirect_limit_enforced():
    def redirect(request):
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "example.com"
        assert request.extensions["sni_hostname"] == "example.com"
        return httpx.Response(302, headers={"Location": "/step2"})

    with patch("socket.getaddrinfo", return_value=[(2, 1, 0, "", ("93.184.216.34", 80))]):
        with _reader_transport(redirect):
            with pytest.raises(SSRFProtectionError, match="Redirect limit exceeded"):
                await fetch_web_page_async("https://example.com/step1", max_redirects=3)


@pytest.mark.asyncio
async def test_redirect_to_private_ip_blocked():
    def dns(host, *_):
        ip = "93.184.216.34" if host == "example.com" else host
        return [(2, 1, 0, "", (ip, 80))]

    def redirect(request):
        assert request.url.host == "93.184.216.34"
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/"})

    with patch("socket.getaddrinfo", side_effect=dns), _reader_transport(redirect):
        with pytest.raises(SSRFProtectionError):
            await fetch_web_page_async("https://example.com/initial")


@pytest.mark.asyncio
async def test_response_payload_capped_at_1mb():
    with patch("socket.getaddrinfo", return_value=[(2, 1, 0, "", ("93.184.216.34", 80))]):
        with _reader_transport(lambda request: httpx.Response(200, stream=_Body())):
            with pytest.raises(ToolError, match="response size limit"):
                await fetch_web_page_async("https://example.com/large", max_bytes=100)


@pytest.mark.asyncio
async def test_request_timeout_raises_tool_error():
    def timeout(request):
        raise httpx.ReadTimeout("Timed out", request=request)

    with patch("socket.getaddrinfo", return_value=[(2, 1, 0, "", ("93.184.216.34", 80))]):
        with _reader_transport(timeout):
            with pytest.raises(ToolError, match="exceeded"):
                await fetch_web_page_async("https://example.com/slow")


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
