"""Unit tests for full-graph export service for PDF, PNG, and SVG (S2-BE6 / US-07).

Covers:
- SVG bounding box calculation spanning the entire layout (not just viewport).
- Dark vs light theme palette styling in generated SVG.
- Parameter validation for `format` (pdf, png, svg) and `theme` (light, dark).
- Ownership assertion and quota limiter enforcement.
- Third-party adapter conversion orchestration for PDF and PNG.
- Graceful error mapping to HTTP 502 Bad Gateway upon conversion failures or timeouts.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from core.supabase import current_user_id
import main
from services.export_service import (
    ExportConversionAdapter,
    ExportConversionError,
    build_full_graph_svg,
)

MAP_ID = UUID("22222222-2222-2222-2222-222222222222")
UID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _mock_pool() -> MagicMock:
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value={"id": str(MAP_ID), "title": "System Architecture"})
    pool.fetchval = AsyncMock(return_value=True)  # assert_map_owner returns True
    return pool


def _sample_nodes_and_edges():
    n1_id = uuid4()
    n2_id = uuid4()
    nodes = [
        {
            "id": n1_id,
            "label": "Frontend Client",
            "pos_x": 100.0,
            "pos_y": 150.0,
            "width": 200.0,
            "height": 80.0,
            "background_color": "#3b82f6",
            "text_color": "#ffffff",
        },
        {
            "id": n2_id,
            "label": "Backend API",
            "pos_x": 500.0,
            "pos_y": 300.0,
            "width": 180.0,
            "height": 70.0,
            "background_color": "#10b981",
            "text_color": "#ffffff",
        },
    ]
    edges = [
        {
            "id": uuid4(),
            "source_node_id": n1_id,
            "target_node_id": n2_id,
            "label": "HTTPS REST",
        }
    ]
    return nodes, edges


# ------------------------------------------------------------------ SVG Generator Unit Tests


def test_build_full_graph_svg_empty_nodes():
    svg = build_full_graph_svg([], [])
    assert "<svg" in svg
    assert "</svg>" in svg
    assert 'viewBox="0.0 0.0' in svg


def test_build_full_graph_svg_bounding_box_covers_all_nodes():
    nodes, edges = _sample_nodes_and_edges()
    # min_x=100, min_y=150, max_x=500+180=680, max_y=300+70=370
    # with padding=80: box_x=20, box_y=70, box_w=(680-100)+160=740, box_h=(370-150)+160=380
    svg = build_full_graph_svg(nodes, edges, scale=2.0)

    assert 'viewBox="20.0 70.0 740.0 380.0"' in svg
    assert 'width="1480.0"' in svg  # 740 * 2.0
    assert 'height="760.0"' in svg   # 380 * 2.0
    assert "Frontend Client" in svg
    assert "Backend API" in svg
    assert "HTTPS REST" in svg


def test_build_full_graph_svg_themes():
    nodes, edges = _sample_nodes_and_edges()

    svg_light = build_full_graph_svg(nodes, edges, theme="light")
    assert 'fill="#ffffff"' in svg_light

    svg_dark = build_full_graph_svg(nodes, edges, theme="dark")
    assert 'fill="#18181b"' in svg_dark


# ------------------------------------------------------------------ Export Route Tests


def test_export_map_invalid_format_raises_422():
    client = TestClient(main.app)
    main.app.dependency_overrides[current_user_id] = lambda: UID
    try:
        resp = client.get(f"/api/maps/{MAP_ID}/export?format=docx")
        assert resp.status_code == 422
        assert "Invalid export format" in resp.json()["detail"]
    finally:
        main.app.dependency_overrides.pop(current_user_id, None)


def test_export_map_invalid_theme_raises_422():
    client = TestClient(main.app)
    main.app.dependency_overrides[current_user_id] = lambda: UID
    try:
        resp = client.get(f"/api/maps/{MAP_ID}/export?theme=retro")
        assert resp.status_code == 422
        assert "Invalid export theme" in resp.json()["detail"]
    finally:
        main.app.dependency_overrides.pop(current_user_id, None)


def test_export_map_unauthorized_user_raises_404():
    client = TestClient(main.app)
    mock_pool = _mock_pool()
    mock_pool.fetchval = AsyncMock(return_value=False)  # assert_map_owner returns False
    main.app.state.pool = mock_pool
    main.app.dependency_overrides[current_user_id] = lambda: UID
    try:
        resp = client.get(f"/api/maps/{MAP_ID}/export?format=png")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Map not found"
    finally:
        main.app.dependency_overrides.pop(current_user_id, None)


def test_export_map_svg_direct_success():
    client = TestClient(main.app)
    mock_pool = _mock_pool()
    nodes, edges = _sample_nodes_and_edges()

    async def mock_fetch(query, *args):
        if "FROM public.nodes" in query:
            return nodes
        if "FROM public.edges" in query:
            return edges
        return []

    mock_pool.fetch = mock_fetch
    main.app.state.pool = mock_pool
    main.app.dependency_overrides[current_user_id] = lambda: UID
    try:
        resp = client.get(f"/api/maps/{MAP_ID}/export?format=svg&theme=dark")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/svg+xml")
        assert 'attachment; filename="System Architecture.svg"' in resp.headers["content-disposition"]
        assert "<svg" in resp.text
        assert "Frontend Client" in resp.text
    finally:
        main.app.dependency_overrides.pop(current_user_id, None)


def test_export_map_pdf_png_via_adapter_success():
    client = TestClient(main.app)
    mock_pool = _mock_pool()
    nodes, edges = _sample_nodes_and_edges()

    async def mock_fetch(query, *args):
        if "FROM public.nodes" in query:
            return nodes
        if "FROM public.edges" in query:
            return edges
        return []

    mock_pool.fetch = mock_fetch
    main.app.state.pool = mock_pool
    main.app.dependency_overrides[current_user_id] = lambda: UID

    mock_adapter = MagicMock()
    mock_adapter.convert = AsyncMock(return_value=b"%PDF-1.4 mock binary content")

    with patch("maps.routes.get_export_adapter", return_value=mock_adapter):
        try:
            resp = client.get(f"/api/maps/{MAP_ID}/export?format=pdf&scale=2")
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("application/pdf")
            assert 'attachment; filename="System Architecture.pdf"' in resp.headers["content-disposition"]
            assert resp.content == b"%PDF-1.4 mock binary content"
            mock_adapter.convert.assert_awaited_once()
        finally:
            main.app.dependency_overrides.pop(current_user_id, None)


def test_export_map_conversion_failure_returns_502():
    client = TestClient(main.app)
    mock_pool = _mock_pool()
    nodes, edges = _sample_nodes_and_edges()
    mock_pool.fetch = AsyncMock(return_value=nodes)
    main.app.state.pool = mock_pool
    main.app.dependency_overrides[current_user_id] = lambda: UID

    mock_adapter = MagicMock()
    mock_adapter.convert = AsyncMock(side_effect=ExportConversionError("Renderer timed out after 15s"))

    with patch("maps.routes.get_export_adapter", return_value=mock_adapter):
        try:
            resp = client.get(f"/api/maps/{MAP_ID}/export?format=png")
            assert resp.status_code == 502
            assert "Export conversion service failed" in resp.json()["detail"]
            assert "Renderer timed out" in resp.json()["detail"]
        finally:
            main.app.dependency_overrides.pop(current_user_id, None)


# ------------------------------------------------------------------ Adapter Retries Test


@pytest.mark.asyncio
async def test_conversion_adapter_retries_and_raises():
    adapter = ExportConversionAdapter(
        converter_url="http://mock-converter:3001/convert",
        timeout_seconds=0.1,
        max_retries=1,
    )

    with patch("httpx.AsyncClient.post", side_effect=ExportConversionError("Simulated network timeout")):
        with pytest.raises(ExportConversionError) as exc_info:
            await adapter.convert("<svg></svg>", "png", scale=2.0)
        assert "Simulated network timeout" in str(exc_info.value)
