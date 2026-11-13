"""Full-graph export service for mind maps (S2-BE6 / US-07).

Constructs scalable vector graphics (SVG) representing the entire mind map layout
(bounding box containing all nodes & edges, not merely the viewport), and orchestrates
conversion to PDF/PNG via a third-party conversion adapter with timeout, retry,
and quota handling.
"""

from __future__ import annotations

import asyncio
import html
import logging
from typing import Any

import httpx
from core.config import settings

logger = logging.getLogger(__name__)


class ExportConversionError(Exception):
    """Raised when SVG conversion fails or converter service is unreachable."""


def build_full_graph_svg(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    theme: str = "light",
    scale: float = 2.0,
) -> str:
    """Build a complete, standalone SVG document for all nodes and edges.

    Computes the full layout bounding box enclosing all graph elements plus padding.
    """
    is_dark = theme.lower() == "dark"

    # Color palettes
    if is_dark:
        bg_color = "#18181b"
        default_node_fill = "#27272a"
        default_node_text = "#f4f4f5"
        default_node_border = "#3f3f46"
        edge_stroke = "#71717a"
        edge_text = "#a1a1aa"
        grid_dot = "#27272a"
    else:
        bg_color = "#ffffff"
        default_node_fill = "#f8f9fa"
        default_node_text = "#18181b"
        default_node_border = "#e4e4e7"
        edge_stroke = "#a1a1aa"
        edge_text = "#52525b"
        grid_dot = "#f4f4f5"

    padding = 80.0

    if not nodes:
        box_x, box_y, box_w, box_h = 0.0, 0.0, 800.0, 600.0
    else:
        min_x = min(float(n.get("pos_x") or 0.0) for n in nodes)
        min_y = min(float(n.get("pos_y") or 0.0) for n in nodes)
        max_x = max(float(n.get("pos_x") or 0.0) + float(n.get("width") or 180.0) for n in nodes)
        max_y = max(float(n.get("pos_y") or 0.0) + float(n.get("height") or 60.0) for n in nodes)

        box_x = min_x - padding
        box_y = min_y - padding
        box_w = max((max_x - min_x) + (2 * padding), 400.0)
        box_h = max((max_y - min_y) + (2 * padding), 300.0)

    scaled_w = box_w * scale
    scaled_h = box_h * scale

    node_map = {str(n.get("id")): n for n in nodes}

    svg_parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{box_x:.1f} {box_y:.1f} {box_w:.1f} {box_h:.1f}" '
        f'width="{scaled_w:.1f}" height="{scaled_h:.1f}">',
        "<defs>",
        '  <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">',
        f'    <path d="M 0 1 L 10 5 L 0 9 z" fill="{edge_stroke}" />',
        "  </marker>",
        '  <pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse">',
        f'    <circle cx="2" cy="2" r="1" fill="{grid_dot}" />',
        "  </pattern>",
        "</defs>",
        f'<rect x="{box_x:.1f}" y="{box_y:.1f}" width="{box_w:.1f}" height="{box_h:.1f}" fill="{bg_color}" />',
        f'<rect x="{box_x:.1f}" y="{box_y:.1f}" width="{box_w:.1f}" height="{box_h:.1f}" fill="url(#grid)" />',
        '<g id="edges">',
    ]

    # Render edges
    for edge in edges:
        src_id = str(edge.get("source_node_id"))
        tgt_id = str(edge.get("target_node_id"))
        src = node_map.get(src_id)
        tgt = node_map.get(tgt_id)

        if not src or not tgt:
            continue

        src_x = float(src.get("pos_x") or 0.0) + (float(src.get("width") or 180.0) / 2.0)
        src_y = float(src.get("pos_y") or 0.0) + (float(src.get("height") or 60.0) / 2.0)
        tgt_x = float(tgt.get("pos_x") or 0.0) + (float(tgt.get("width") or 180.0) / 2.0)
        tgt_y = float(tgt.get("pos_y") or 0.0) + (float(tgt.get("height") or 60.0) / 2.0)

        dx = tgt_x - src_x
        cx1 = src_x + (dx * 0.5)
        cy1 = src_y
        cx2 = src_x + (dx * 0.5)
        cy2 = tgt_y

        path_d = f"M {src_x:.1f} {src_y:.1f} C {cx1:.1f} {cy1:.1f}, {cx2:.1f} {cy2:.1f}, {tgt_x:.1f} {tgt_y:.1f}"
        svg_parts.append(
            f'  <path d="{path_d}" stroke="{edge_stroke}" stroke-width="2" fill="none" marker-end="url(#arrow)" />'
        )

        edge_label = edge.get("label")
        if edge_label and str(edge_label).strip():
            mid_x = (src_x + tgt_x) / 2.0
            mid_y = (src_y + tgt_y) / 2.0
            safe_edge_label = html.escape(str(edge_label).strip())
            svg_parts.append(
                f'  <text x="{mid_x:.1f}" y="{mid_y - 6.0:.1f}" text-anchor="middle" '
                f'font-size="12" fill="{edge_text}" font-family="system-ui, -apple-system, sans-serif">'
                f"{safe_edge_label}</text>"
            )

    svg_parts.append("</g>")
    svg_parts.append('<g id="nodes">')

    # Render nodes
    for node in nodes:
        nx = float(node.get("pos_x") or 0.0)
        ny = float(node.get("pos_y") or 0.0)
        nw = float(node.get("width") or 180.0)
        nh = float(node.get("height") or 60.0)

        fill = node.get("background_color") or default_node_fill
        text_color = node.get("text_color") or default_node_text
        border = default_node_border

        label = str(node.get("label") or "Untitled").strip()
        safe_label = html.escape(label)

        svg_parts.append(
            f'  <rect x="{nx:.1f}" y="{ny:.1f}" width="{nw:.1f}" height="{nh:.1f}" '
            f'rx="8" ry="8" fill="{fill}" stroke="{border}" stroke-width="1.5" />'
        )

        center_x = nx + (nw / 2.0)
        center_y = ny + (nh / 2.0) + 4.0
        svg_parts.append(
            f'  <text x="{center_x:.1f}" y="{center_y:.1f}" text-anchor="middle" '
            f'font-size="14" font-weight="500" fill="{text_color}" '
            f'font-family="system-ui, -apple-system, sans-serif">{safe_label}</text>'
        )

    svg_parts.append("</g>")
    svg_parts.append("</svg>")
    return "\n".join(svg_parts)


class ExportConversionAdapter:
    """Adapter communicating with a headless conversion service to render SVG to PDF or PNG."""

    def __init__(
        self,
        converter_url: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int = 2,
    ) -> None:
        self.converter_url = converter_url or settings.EXPORT_CONVERTER_URL
        self.timeout_seconds = timeout_seconds or settings.EXPORT_TIMEOUT_SECONDS
        self.max_retries = max_retries

    async def convert(
        self,
        svg_content: str,
        target_format: str,
        scale: float = 2.0,
    ) -> bytes:
        """Post SVG to the conversion adapter with strict timeout and retries.

        Raises:
            ExportConversionError: If conversion fails, times out, or returns a 5xx error.
        """
        fmt = target_format.lower()
        if fmt == "svg":
            return svg_content.encode("utf-8")

        payload = {
            "svg": svg_content,
            "format": fmt,
            "scale": scale,
        }

        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 2):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    resp = await client.post(self.converter_url, json=payload)
                    if resp.status_code == 200:
                        return resp.content
                    if resp.status_code in (502, 503, 504):
                        last_error = ExportConversionError(
                            f"Converter upstream error {resp.status_code}: {resp.text[:200]}"
                        )
                    else:
                        raise ExportConversionError(
                            f"Conversion failed with status {resp.status_code}: {resp.text[:200]}"
                        )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = ExportConversionError(f"Converter connection/timeout error: {exc}")

            if attempt <= self.max_retries:
                logger.warning("Export conversion attempt %d failed (%s), retrying...", attempt, last_error)
                await asyncio.sleep(0.3 * attempt)

        raise ExportConversionError(f"Export service unavailable after {self.max_retries + 1} attempts: {last_error}")


_default_adapter = ExportConversionAdapter()


def get_export_adapter() -> ExportConversionAdapter:
    """Return singleton export conversion adapter."""
    return _default_adapter
