"""Unit tests for Supabase Storage cleanup on map deletion."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from uploads.service import delete_map_storage_files


@pytest.mark.asyncio
async def test_delete_map_storage_files_removes_db_and_prefix_keys():
    user_id = uuid.uuid4()
    map_id = uuid.uuid4()

    # Mock DB pool returning recorded seed asset storage keys (matches schema.sql storage_key)
    mock_pool = MagicMock()
    mock_pool.fetch = AsyncMock(
        return_value=[
            {"storage_key": f"{user_id}/{map_id}/job-1/0_notes.pdf"},
            {"storage_key": f"{user_id}/{map_id}/job-1/1_diagram.png"},
        ]
    )

    # Mock Supabase admin.storage
    mock_storage = MagicMock()
    mock_bucket = MagicMock()
    mock_bucket.list.return_value = [{"name": "job-1"}]
    mock_bucket.remove.return_value = [{"name": "deleted"}]
    mock_storage.from_.return_value = mock_bucket

    with patch("uploads.service.admin.storage", mock_storage):
        deleted_keys = await delete_map_storage_files(
            user_id=user_id,
            map_id=map_id,
            pool=mock_pool,
        )

    # Verify bucket.remove was called
    assert mock_bucket.remove.called
    removed_arg = mock_bucket.remove.call_args[0][0]
    assert f"{user_id}/{map_id}/job-1/0_notes.pdf" in removed_arg
    assert f"{user_id}/{map_id}/job-1/1_diagram.png" in removed_arg
    assert len(deleted_keys) >= 2


@pytest.mark.asyncio
async def test_delete_map_storage_files_handles_empty_cleanly():
    user_id = uuid.uuid4()
    map_id = uuid.uuid4()

    mock_pool = MagicMock()
    mock_pool.fetch = AsyncMock(return_value=[])

    mock_storage = MagicMock()
    mock_bucket = MagicMock()
    mock_bucket.list.return_value = []
    mock_storage.from_.return_value = mock_bucket

    with patch("uploads.service.admin.storage", mock_storage):
        deleted_keys = await delete_map_storage_files(
            user_id=user_id,
            map_id=map_id,
            pool=mock_pool,
        )

    assert deleted_keys == []


@pytest.mark.asyncio
async def test_delete_map_storage_files_raises_on_storage_failure():
    """Verify Problem B: Storage deletion failure raises RuntimeError to prevent DB deletion."""
    user_id = uuid.uuid4()
    map_id = uuid.uuid4()

    mock_pool = MagicMock()
    mock_pool.fetch = AsyncMock(
        return_value=[
            {"storage_key": f"{user_id}/{map_id}/job-1/0_notes.pdf"},
        ]
    )

    mock_storage = MagicMock()
    mock_bucket = MagicMock()
    mock_bucket.list.return_value = []
    mock_bucket.remove.side_effect = Exception("Supabase Storage 503 Unavailable")
    mock_storage.from_.return_value = mock_bucket

    with (
        patch("uploads.service.admin.storage", mock_storage),
        pytest.raises(RuntimeError, match="Storage deletion failed for keys"),
    ):
        await delete_map_storage_files(
            user_id=user_id,
            map_id=map_id,
            pool=mock_pool,
        )
