"""Database alignment regression tests retained from PR #64 after main sync."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from db.repository import create_mind_map, get_or_create_chat_thread


@pytest.mark.asyncio
async def test_chat_thread_returns_existing_id() -> None:
    thread_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.return_value = {"id": thread_id}

    result = await get_or_create_chat_thread(conn, uuid4(), uuid4())

    assert result == thread_id
    conn.fetchrow.assert_awaited_once()


@pytest.mark.asyncio
async def test_chat_thread_creation_is_conflict_safe() -> None:
    thread_id = uuid4()
    conn = AsyncMock()
    conn.fetchrow.side_effect = [None, None, {"id": thread_id}]

    result = await get_or_create_chat_thread(conn, uuid4(), uuid4())

    assert result == thread_id
    assert "ON CONFLICT (map_id) DO NOTHING" in conn.fetchrow.call_args_list[1].args[0]
    assert conn.fetchrow.await_count == 3


@pytest.mark.asyncio
async def test_create_mind_map_normalizes_title() -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = {"id": uuid4(), "title": "Untitled mind map"}
    await create_mind_map(conn, uuid4(), "    ")
    assert conn.fetchrow.call_args.args[2] == "Untitled mind map"


@pytest.mark.asyncio
async def test_create_mind_map_clips_long_title() -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = {"id": uuid4(), "title": "a" * 255}
    await create_mind_map(conn, uuid4(), "a" * 300)
    assert conn.fetchrow.call_args.args[2] == "a" * 255
