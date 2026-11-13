"""
Unit and integration tests for server-side search, sorting, pagination,
and strict OCC save integrity (ETag / If-Match).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from maps.routes import _parse_version_token


def test_parse_version_token_iso():
    iso_str = "2026-11-12T16:30:00.123456+00:00"
    dt = _parse_version_token(iso_str)
    assert dt.isoformat() == iso_str


def test_parse_version_token_with_quotes_and_w():
    iso_str = 'W/"2026-11-12T16:30:00.123456+00:00"'
    dt = _parse_version_token(iso_str)
    assert dt.isoformat() == "2026-11-12T16:30:00.123456+00:00"


def test_parse_version_token_zulu():
    dt = _parse_version_token("2026-11-12T16:30:00Z")
    assert dt.tzinfo == UTC


def test_parse_version_token_invalid():
    with pytest.raises(HTTPException) as exc:
        _parse_version_token("invalid-date-string")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_search_maps_query_and_pagination():
    from db.repository import search_maps

    mock_conn = AsyncMock()
    mock_conn.fetchval.return_value = 5
    mock_conn.fetch.return_value = [
        {
            "id": uuid4(),
            "title": "Machine Learning Map",
            "description": "Neural networks notes",
            "is_public": False,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "node_count": 12,
            "latest_job_status": "completed",
            "latest_job_progress": 100,
            "latest_job_id": uuid4(),
        }
    ]

    items, total = await search_maps(
        mock_conn,
        user_id=uuid4(),
        query="neural",
        sort="title_asc",
        limit=10,
        offset=0,
    )

    assert total == 5
    assert len(items) == 1
    assert items[0]["title"] == "Machine Learning Map"
    assert "WHERE" in mock_conn.fetch.call_args[0][0]
    assert "ORDER BY m.title ASC" in mock_conn.fetch.call_args[0][0]


@pytest.mark.asyncio
async def test_update_mind_map_occ_conflict():
    from db.repository import update_mind_map_occ

    mock_conn = AsyncMock()
    # 0 rows returned from UPDATE due to updated_at mismatch
    mock_conn.fetchrow.return_value = None
    # existing_updated_at exists in DB (different timestamp)
    mock_conn.fetchval.return_value = datetime(2026, 11, 12, 17, 0, 0, tzinfo=UTC)

    map_id = uuid4()
    user_id = uuid4()
    stale_timestamp = datetime(2026, 11, 12, 16, 0, 0, tzinfo=UTC)

    row, conflict_status = await update_mind_map_occ(
        mock_conn,
        map_id=map_id,
        user_id=user_id,
        expected_updated_at=stale_timestamp,
        title="Updated Title",
    )

    assert row is None
    assert conflict_status == "CONFLICT"


@pytest.mark.asyncio
async def test_update_mind_map_occ_success():
    from db.repository import update_mind_map_occ

    map_id = uuid4()
    user_id = uuid4()
    now_dt = datetime.now(UTC)

    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = {
        "id": map_id,
        "title": "New Title",
        "description": "Updated Description",
        "is_public": False,
        "created_at": now_dt,
        "updated_at": now_dt,
    }

    row, conflict_status = await update_mind_map_occ(
        mock_conn,
        map_id=map_id,
        user_id=user_id,
        expected_updated_at=now_dt,
        title="New Title",
        description="Updated Description",
    )

    assert conflict_status is None
    assert row["title"] == "New Title"
