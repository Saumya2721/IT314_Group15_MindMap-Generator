"""SSRF-guarded web page reader tool and HTML-to-Markdown parser.

Fetches web pages or URL seeds with strict SSRF protections:
- DNS pre-resolution & blocking of loopback, private, link-local, and cloud metadata IPs
- Maximum redirect cap (<= 3 redirects) with validation at every hop
- Maximum payload cap (<= 1 MB)
- Strict request timeout (<= 5.0s)
- Clean HTML to Markdown conversion stripping scripts, styles, and boilerplate navigation
"""

from __future__ import annotations

import html
import ipaddress
import logging
import socket
import urllib.parse
from typing import Any

import httpx
import lxml.html
from providers.base import ToolSpec
from tools.base import Tool, ToolError, ToolResult

logger = logging.getLogger(__name__)

# Security defaults
MAX_PAYLOAD_BYTES = 1024 * 1024  # 1 MB response cap
DEFAULT_TIMEOUT_S = 5.0  # 5s request timeout
MAX_REDIRECTS = 3  # Maximum HTTP redirect hops

# Blocked IP ranges (RFC 1918, RFC 3927 link-local, loopback, multicast, reserved)
BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),  # Carrier-grade NAT
    ipaddress.ip_network("127.0.0.0/8"),  # Loopback
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local & cloud metadata (169.254.169.254)
    ipaddress.ip_network("172.16.0.0/12"),  # Private Class B
    ipaddress.ip_network("192.0.0.0/24"),  # IETF assignments
    ipaddress.ip_network("192.0.2.0/24"),  # TEST-NET-1
    ipaddress.ip_network("192.168.0.0/16"),  # Private Class C
    ipaddress.ip_network("198.18.0.0/15"),  # Benchmarking
    ipaddress.ip_network("198.51.100.0/24"),  # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),  # TEST-NET-3
    ipaddress.ip_network("224.0.0.0/4"),  # Multicast
    ipaddress.ip_network("240.0.0.0/4"),  # Reserved
    ipaddress.ip_network("255.255.255.255/32"),  # Broadcast
    # IPv6 ranges
    ipaddress.ip_network("::/128"),  # Unspecified
    ipaddress.ip_network("::1/128"),  # Loopback
    ipaddress.ip_network("fc00::/7"),  # Unique local (private)
    ipaddress.ip_network("fe80::/10"),  # Link-local
    ipaddress.ip_network("ff00::/8"),  # Multicast
    ipaddress.ip_network("::ffff:0:0/96"),  # IPv4-mapped IPv6
]

METADATA_HOSTNAMES = {
    "169.254.169.254",
    "metadata.google.internal",
    "instance-data",
    "metadata.internal",
    "metadata",
    "localhost",
}

UNWANTED_HTML_TAGS = (
    "script",
    "style",
    "nav",
    "header",
    "footer",
    "aside",
    "noscript",
    "svg",
    "iframe",
    "form",
)


class SSRFProtectionError(ToolError):
    """Raised when a request is blocked by SSRF protection rules."""


def validate_url_and_resolve_ip(url: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Validate URL scheme and resolve hostname to verify no private/loopback/metadata addresses."""
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise SSRFProtectionError(f"Unsupported URL scheme '{scheme}': only http and https are permitted.")

    hostname = parsed.hostname
    if not hostname:
        raise SSRFProtectionError(f"URL is missing a valid hostname: {url}")

    if (
        hostname.lower() in METADATA_HOSTNAMES
        or hostname.lower().endswith(".internal")
        or hostname.lower().endswith(".local")
    ):
        raise SSRFProtectionError(f"Access to metadata or local endpoint '{hostname}' is blocked.")

    # Resolve IP addresses
    try:
        addr_info = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise SSRFProtectionError(f"DNS resolution failed for hostname '{hostname}': {exc}") from exc

    resolved_ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for entry in addr_info:
        sockaddr = entry[4]
        ip_str = sockaddr[0]
        ip = ipaddress.ip_address(ip_str)

        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped

        # Check loopback, private, link-local, cloud metadata
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise SSRFProtectionError(f"Blocked access to private or local IP address {ip} for host '{hostname}'.")

        for blocked_net in BLOCKED_NETWORKS:
            if ip in blocked_net:
                raise SSRFProtectionError(
                    f"Blocked access to restricted IP address {ip} (in {blocked_net}) for host '{hostname}'."
                )

        resolved_ips.append(ip)

    if not resolved_ips:
        raise SSRFProtectionError(f"No IP addresses found for hostname '{hostname}'.")

    return resolved_ips


def html_to_markdown(html_content: str, base_url: str | None = None) -> tuple[str, str | None]:
    """Convert HTML content into clean Markdown, stripping scripts, styles, and boilerplate navigation."""
    if not html_content or not html_content.strip():
        return "", None

    try:
        doc = lxml.html.fromstring(html_content)
    except Exception as exc:
        logger.warning("Failed to parse HTML with lxml (%s), falling back to raw text", exc)
        return html.unescape(html_content).strip(), None

    title: str | None = None
    title_el = doc.find(".//title")
    if title_el is not None and title_el.text:
        title = title_el.text.strip()

    # Drop script, style, nav, header, footer, aside, and other boilerplate elements
    for tag in UNWANTED_HTML_TAGS:
        for el in doc.xpath(f"//{tag}"):
            el.drop_tree()

    # Walk body elements and build Markdown lines
    lines: list[str] = []
    if title:
        lines.append(f"# {title}\n")

    def _process_node(node: Any, depth: int = 0) -> None:
        tag = getattr(node, "tag", None)
        if not isinstance(tag, str):
            return

        tag = tag.lower()

        # Headings
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            text = node.text_content().strip()
            if text:
                level = int(tag[1])
                prefix = "#" * level
                lines.append(f"\n{prefix} {text}\n")
            return

        # Paragraphs & divs
        if tag in ("p", "div", "section", "article"):
            text = node.text_content().strip()
            if text and len(node) == 0:  # Leaf text container
                lines.append(f"\n{text}\n")
            else:
                for child in node:
                    _process_node(child, depth)
            return

        # List items
        if tag == "li":
            text = node.text_content().strip()
            if text:
                indent = "  " * depth
                lines.append(f"{indent}- {text}")
            return

        # Lists
        if tag in ("ul", "ol"):
            for child in node:
                _process_node(child, depth + 1)
            return

        # Blockquotes
        if tag == "blockquote":
            text = node.text_content().strip()
            if text:
                lines.append(f"\n> {text}\n")
            return

        # Code blocks
        if tag in ("pre", "code"):
            text = node.text_content().strip()
            if text:
                lines.append(f"\n```\n{text}\n```\n")
            return

        # Tables
        if tag == "table":
            rows = node.xpath(".//tr")
            table_lines: list[str] = []
            for r_idx, row in enumerate(rows):
                cells = [c.text_content().strip().replace("\n", " ") for c in row.xpath("./th | ./td")]
                if not any(cells):
                    continue
                table_lines.append("| " + " | ".join(cells) + " |")
                if r_idx == 0:
                    table_lines.append("| " + " | ".join(["---"] * len(cells)) + " |")
            if table_lines:
                lines.append("\n" + "\n".join(table_lines) + "\n")
            return

        # Recurse children
        for child in node:
            _process_node(child, depth)

    body = doc.find(".//body")
    if body is not None:
        _process_node(body)
    else:
        _process_node(doc)

    raw_markdown = "\n".join(lines).strip()
    # Normalize multiple newlines
    import re

    cleaned = re.sub(r"\n{3,}", "\n\n", raw_markdown)
    return cleaned, title


async def fetch_web_page_async(
    url: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_redirects: int = MAX_REDIRECTS,
    max_bytes: int = MAX_PAYLOAD_BYTES,
) -> tuple[str, str | None]:
    """Asynchronously fetch a web page with strict SSRF guards, redirect limits, and size caps."""
    current_url = url
    redirect_count = 0

    try:
        async with httpx.AsyncClient(
            timeout=timeout_s,
            follow_redirects=False,
            verify=True,
        ) as client:
            while True:
                validate_url_and_resolve_ip(current_url)

                resp = await client.get(
                    current_url,
                    headers={"User-Agent": "MindMap-Reader/1.0 (SSRF-Guarded)"},
                )
                if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
                    redirect_count += 1
                    if redirect_count > max_redirects:
                        raise SSRFProtectionError(
                            f"Redirect limit exceeded (maximum {max_redirects} redirects allowed)."
                        )
                    location = resp.headers.get("location")
                    if not location:
                        raise ToolError("Redirect response missing Location header.")
                    current_url = urllib.parse.urljoin(current_url, location)
                    continue

                resp.raise_for_status()

                raw_bytes = resp.content[:max_bytes]
                raw_html = raw_bytes.decode("utf-8", errors="replace")
                markdown, title = html_to_markdown(raw_html, base_url=current_url)
                return markdown, title

    except httpx.TimeoutException as exc:
        raise ToolError(f"Request timed out after {timeout_s}s: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        raise ToolError(f"HTTP request failed with status {exc.response.status_code}: {exc}") from exc
    except httpx.RequestError as exc:
        raise ToolError(f"HTTP request failed: {exc}") from exc


def fetch_web_page(
    url: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_redirects: int = MAX_REDIRECTS,
    max_bytes: int = MAX_PAYLOAD_BYTES,
) -> tuple[str, str | None]:
    """Synchronous entrypoint for fetch_web_page_async."""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                asyncio.run,
                fetch_web_page_async(url, timeout_s, max_redirects, max_bytes),
            ).result()
    return asyncio.run(fetch_web_page_async(url, timeout_s, max_redirects, max_bytes))


class WebPageReaderTool(Tool):
    """Tool that fetches and reads web page content as clean Markdown with SSRF protection."""

    def __init__(
        self,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_redirects: int = MAX_REDIRECTS,
        max_bytes: int = MAX_PAYLOAD_BYTES,
    ):
        self._timeout_s = min(timeout_s, 5.0)  # Enforce <= 5s
        self._max_redirects = min(max_redirects, 3)  # Enforce <= 3 redirects
        self._max_bytes = min(max_bytes, 1024 * 1024)  # Enforce <= 1 MB

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

    async def invoke(self, *, url: str = "", **kwargs: Any) -> list[ToolResult]:
        target_url = url or str(kwargs.get("url", ""))
        if not target_url:
            raise ToolError("URL argument is required for web_page_reader.")

        try:
            markdown, title = await fetch_web_page_async(
                url=target_url,
                timeout_s=self._timeout_s,
                max_redirects=self._max_redirects,
                max_bytes=self._max_bytes,
            )
        except SSRFProtectionError as exc:
            raise ToolError(f"SSRF protection blocked request: {exc}") from exc
        except Exception as exc:
            raise ToolError(f"Failed to read web page: {exc}") from exc

        return [
            ToolResult(
                content=markdown,
                source_title=title or "Web Page",
                source_url=target_url,
            )
        ]
