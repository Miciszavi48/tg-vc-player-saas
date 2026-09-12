from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.broadcast_service_v2 import (
    BROADCAST_RECIPIENT_BATCH_SIZE,
    BroadcastServiceV2,
)


def _mock_session_for_count_and_batch(
    count: int,
    batches: dict[int, list[int]],
) -> MagicMock:
    """Return session mock: first execute is count scalar, then batch scalars by offset."""
    sess = AsyncMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    calls: list = []

    async def _execute(_stmt):
        calls.append(_stmt)
        mock_result = MagicMock()
        if len(calls) == 1:
            mock_result.scalar.return_value = count
        else:
            offset = (len(calls) - 2) * BROADCAST_RECIPIENT_BATCH_SIZE
            mock_result.scalars.return_value.all.return_value = batches.get(offset, [])
        return mock_result

    sess.execute = _execute
    sess._calls = calls
    return sess


@pytest.mark.asyncio
async def test_count_recipients_users_excludes_banned():
    sess = AsyncMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock()
    mock_result.scalar.return_value = 3
    sess.execute = AsyncMock(return_value=mock_result)

    with patch("app.services.broadcast_service_v2.async_session", return_value=sess):
        total = await BroadcastServiceV2._count_recipients("users", "all")

    assert total == 3
    stmt = sess.execute.await_args.args[0]
    compiled = str(stmt.whereclause)
    assert "is_banned" in compiled


@pytest.mark.asyncio
async def test_count_recipients_users_7d_filter_in_sql():
    sess = AsyncMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock()
    mock_result.scalar.return_value = 1
    sess.execute = AsyncMock(return_value=mock_result)

    with patch("app.services.broadcast_service_v2.async_session", return_value=sess):
        await BroadcastServiceV2._count_recipients("users", "7d")

    stmt = sess.execute.await_args.args[0]
    compiled = str(stmt.whereclause)
    assert "last_seen" in compiled


@pytest.mark.asyncio
async def test_get_recipient_batch_groups_active_and_offset():
    sess = AsyncMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [-100, -200]
    sess.execute = AsyncMock(return_value=mock_result)

    with patch("app.services.broadcast_service_v2.async_session", return_value=sess):
        batch = await BroadcastServiceV2._get_recipient_batch(
            "groups", "all", offset=10, limit=50,
        )

    assert batch == [-100, -200]
    stmt = sess.execute.await_args.args[0]
    compiled = str(stmt)
    assert "chat_id" in compiled
    assert "status" in compiled


@pytest.mark.asyncio
async def test_get_recipient_batch_groups_30d_activity_in_sql():
    sess = AsyncMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [-100]
    sess.execute = AsyncMock(return_value=mock_result)

    with patch("app.services.broadcast_service_v2.async_session", return_value=sess):
        batch = await BroadcastServiceV2._get_recipient_batch(
            "groups", "30d", offset=0, limit=500,
        )

    assert batch == [-100]
    stmt = sess.execute.await_args.args[0]
    compiled = str(stmt.whereclause)
    assert "last_activity" in compiled


@pytest.mark.asyncio
async def test_get_recipients_compat_collects_batches():
    user_ids = list(range(1, 1201))

    async def fake_count(scope, filter_type="all"):
        return len(user_ids)

    batch_mock = AsyncMock(
        side_effect=lambda scope, filter_type, offset, limit: user_ids[
            offset : offset + limit
        ],
    )

    with (
        patch.object(BroadcastServiceV2, "_count_recipients", side_effect=fake_count),
        patch.object(BroadcastServiceV2, "_get_recipient_batch", batch_mock),
    ):
        result = await BroadcastServiceV2._get_recipients("users", "all")

    assert result == user_ids
    assert batch_mock.await_count == 3


def _make_broadcast(**overrides) -> SimpleNamespace:
    defaults = dict(
        id=1, admin_id=100, mode="send", target_scope="users",
        payload_type="text", text_content="Hello",
        entities_json=None, caption=None, caption_entities_json=None,
        file_id=None, source_chat_id=100, source_message_id=1,
        status="pending", interval_hours=None, filter_type="all",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_execute_uses_count_and_batches_not_full_list():
    bc = _make_broadcast(id=5, target_scope="users")
    user_ids = list(range(1, 1201))

    async def fake_count(scope, filter_type="all"):
        return len(user_ids)

    async def fake_batch(scope, filter_type, offset, limit):
        return user_ids[offset : offset + limit]

    mock_client = AsyncMock()

    with (
        patch("app.services.broadcast_service_v2.acquire_lock", return_value="tok"),
        patch("app.services.broadcast_service_v2.release_lock", return_value=True),
        patch("app.services.broadcast_service_v2.set_bc_active"),
        patch("app.services.broadcast_service_v2.clear_bc_active"),
        patch("app.services.broadcast_service_v2.clear_bc_progress"),
        patch("app.services.broadcast_service_v2.update_bc_progress"),
        patch("app.services.broadcast_service_v2.get_bc_progress", return_value=(0, 0, 0)),
        patch("app.services.broadcast_service_v2.track_event"),
        patch("app.services.broadcast_service_v2.broadcast_repo") as mock_repo,
        patch.object(BroadcastServiceV2, "_count_recipients", side_effect=fake_count) as count_mock,
        patch.object(BroadcastServiceV2, "_get_recipient_batch", side_effect=fake_batch) as batch_mock,
        patch("app.services.broadcast_service_v2.asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_repo.get_by_id = AsyncMock(return_value=bc)
        mock_repo.update_status = AsyncMock()
        mock_repo.update_total = AsyncMock()
        mock_repo.finish = AsyncMock()

        await BroadcastServiceV2.execute(mock_client, 5)

    count_mock.assert_awaited_once()
    assert batch_mock.await_count == 3
    mock_repo.update_total.assert_called_once_with(5, 1200)
    assert mock_client.send_message.await_count == 1200


@pytest.mark.asyncio
async def test_execute_resumes_from_cursor_offset():
    bc = _make_broadcast(id=6, target_scope="users")
    user_ids = list(range(100, 200))

    async def fake_count(scope, filter_type="all"):
        return len(user_ids)

    async def fake_batch(scope, filter_type, offset, limit):
        return user_ids[offset : offset + limit]

    mock_client = AsyncMock()

    with (
        patch("app.services.broadcast_service_v2.acquire_lock", return_value="tok"),
        patch("app.services.broadcast_service_v2.release_lock", return_value=True),
        patch("app.services.broadcast_service_v2.set_bc_active"),
        patch("app.services.broadcast_service_v2.clear_bc_active"),
        patch("app.services.broadcast_service_v2.clear_bc_progress"),
        patch("app.services.broadcast_service_v2.update_bc_progress") as progress_mock,
        patch("app.services.broadcast_service_v2.get_bc_progress", return_value=(25, 20, 2)),
        patch("app.services.broadcast_service_v2.track_event"),
        patch("app.services.broadcast_service_v2.broadcast_repo") as mock_repo,
        patch.object(BroadcastServiceV2, "_count_recipients", side_effect=fake_count),
        patch.object(BroadcastServiceV2, "_get_recipient_batch", side_effect=fake_batch) as batch_mock,
        patch("app.services.broadcast_service_v2.asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_repo.get_by_id = AsyncMock(return_value=bc)
        mock_repo.update_status = AsyncMock()
        mock_repo.update_total = AsyncMock()
        mock_repo.finish = AsyncMock()

        await BroadcastServiceV2.execute(mock_client, 6)

    batch_mock.assert_awaited()
    first_call = batch_mock.await_args_list[0]
    assert first_call.kwargs["offset"] == 25
    args = mock_repo.finish.call_args[0]
    assert args[2] == 95
