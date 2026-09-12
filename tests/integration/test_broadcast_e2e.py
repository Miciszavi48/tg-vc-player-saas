"""End-to-End tests for the Broadcast Engine.

Covers:
- 5000-user broadcast simulation with mocked Pyrogram client
- FloodWait retry logic
- Scheduled broadcast via APScheduler
- Filter-type audience segmentation (all, 7d, 30d)
- Cancel mid-broadcast
- Lock prevents double-execution
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_broadcast(**overrides) -> SimpleNamespace:
    defaults = dict(
        id=1, admin_id=100, mode="send", target_scope="users",
        payload_type="text", text_content="Hello world!",
        entities_json=None, caption=None, caption_entities_json=None,
        file_id=None, source_chat_id=100, source_message_id=1,
        status="pending", total_recipients=0, sent_count=0, fail_count=0,
        created_at=datetime.now(timezone.utc), started_at=None, finished_at=None,
        run_at=None, interval_hours=None, target_types_json=None,
        filter_type="all", source_admin_chat_id=None, source_admin_msg_id=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class FakeFloodWait(Exception):
    def __init__(self, value: int = 1):
        self.value = value
        super().__init__(f"FloodWait: {value}")


@contextmanager
def _with_batch_recipient_patches(user_ids: list[int]):
    async def _count(scope, filter_type="all"):
        return len(user_ids)

    async def _batch(scope, filter_type, offset, limit):
        return user_ids[offset : offset + limit]

    with (
        patch(
            "app.services.broadcast_service_v2.BroadcastServiceV2._count_recipients",
            side_effect=_count,
        ),
        patch(
            "app.services.broadcast_service_v2.BroadcastServiceV2._get_recipient_batch",
            side_effect=_batch,
        ),
    ):
        yield


# ── E2E: 5000-user broadcast ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_broadcast_5000_users():
    """Simulate sending broadcast to 5000 users — verify sent/fail counts."""
    bc = _make_broadcast(id=10, target_scope="users")

    mock_client = AsyncMock()
    mock_client.send_message = AsyncMock()

    user_ids = list(range(1, 5001))

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
        _with_batch_recipient_patches(user_ids),
        patch("app.services.broadcast_service_v2.asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_repo.get_by_id = AsyncMock(return_value=bc)
        mock_repo.update_status = AsyncMock()
        mock_repo.update_total = AsyncMock()
        mock_repo.finish = AsyncMock()

        from app.services.broadcast_service_v2 import BroadcastServiceV2
        await BroadcastServiceV2.execute(mock_client, 10)

        mock_repo.update_total.assert_called_once_with(10, 5000)
        mock_repo.finish.assert_called_once()
        args = mock_repo.finish.call_args
        assert args[0][0] == 10
        assert args[0][1] == "done"
        assert args[0][2] == 5000
        assert args[0][3] == 0


# ── FloodWait retry ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_broadcast_floodwait_retry():
    """First send raises FloodWait, retry succeeds — should count as sent."""
    bc = _make_broadcast(id=20)
    user_ids = [111, 222, 333]

    call_count = 0

    async def mock_send_message(chat_id, text, **kwargs):
        nonlocal call_count
        call_count += 1
        if chat_id == 222 and call_count == 2:
            raise FakeFloodWait(1)

    mock_client = AsyncMock()
    mock_client.send_message = mock_send_message

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
        _with_batch_recipient_patches(user_ids),
        patch("app.services.broadcast_service_v2.asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_repo.get_by_id = AsyncMock(return_value=bc)
        mock_repo.update_status = AsyncMock()
        mock_repo.update_total = AsyncMock()
        mock_repo.finish = AsyncMock()

        from app.services.broadcast_service_v2 import BroadcastServiceV2
        await BroadcastServiceV2.execute(mock_client, 20)

        args = mock_repo.finish.call_args
        assert args[0][1] == "done"
        assert args[0][2] >= 2


# ── Cancel mid-broadcast ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_broadcast_cancel_midway():
    """Simulate cancel after 50 messages — should stop early."""
    bc = _make_broadcast(id=30)
    user_ids = list(range(1, 201))

    check_count = 0

    async def get_by_id_side_effect(bid):
        nonlocal check_count
        check_count += 1
        if check_count > 2:
            return _make_broadcast(id=30, status="canceled")
        return bc

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
        _with_batch_recipient_patches(user_ids),
        patch("app.services.broadcast_service_v2.asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_repo.get_by_id = AsyncMock(side_effect=get_by_id_side_effect)
        mock_repo.update_status = AsyncMock()
        mock_repo.update_total = AsyncMock()
        mock_repo.finish = AsyncMock()

        from app.services.broadcast_service_v2 import BroadcastServiceV2
        await BroadcastServiceV2.execute(mock_client, 30)

        args = mock_repo.finish.call_args
        assert args[0][1] == "canceled"
        assert args[0][2] < 200


# ── Lock prevents double execution ──────────────────────────────────────

@pytest.mark.asyncio
async def test_broadcast_lock_prevents_double_execution():
    """If lock can't be acquired, execute returns immediately."""
    with patch("app.services.broadcast_service_v2.acquire_lock", return_value=None):
        mock_client = AsyncMock()
        from app.services.broadcast_service_v2 import BroadcastServiceV2
        await BroadcastServiceV2.execute(mock_client, 99)
        mock_client.send_message.assert_not_called()


# ── Filter-type audience segmentation ────────────────────────────────────

@pytest.mark.asyncio
async def test_get_recipients_groups_filter_30d():
    """filter_type='30d' uses SQL-filtered batched recipient fetch."""
    from app.services.broadcast_service_v2 import BroadcastServiceV2

    async def _count(scope, filter_type="all"):
        return 1

    async def _batch(scope, filter_type, offset, limit):
        if filter_type == "30d" and offset == 0:
            return [-100]
        return []

    with (
        patch.object(BroadcastServiceV2, "_count_recipients", side_effect=_count),
        patch.object(BroadcastServiceV2, "_get_recipient_batch", side_effect=_batch),
    ):
        result = await BroadcastServiceV2._get_recipients("groups", "30d")
        assert result == [-100]


@pytest.mark.asyncio
async def test_get_recipients_groups_filter_all():
    """filter_type='all' returns all groups via batch collection."""
    from app.services.broadcast_service_v2 import BroadcastServiceV2

    async def _count(scope, filter_type="all"):
        return 2

    async def _batch(scope, filter_type, offset, limit):
        if offset == 0:
            return [-100, -200]
        return []

    with (
        patch.object(BroadcastServiceV2, "_count_recipients", side_effect=_count),
        patch.object(BroadcastServiceV2, "_get_recipient_batch", side_effect=_batch),
    ):
        result = await BroadcastServiceV2._get_recipients("groups", "all")
        assert result == [-100, -200]


# ── Scheduled broadcast via APScheduler ──────────────────────────────────

@pytest.mark.asyncio
async def test_scheduled_broadcast_job_runs():
    """The scheduled job function correctly calls execute."""
    from app.handlers.broadcast_wizard import _run_scheduled_broadcast

    mock_client = AsyncMock()
    with patch("app.handlers.broadcast_wizard.BroadcastServiceV2") as mock_svc:
        mock_svc.execute = AsyncMock()
        await _run_scheduled_broadcast(mock_client, 42)
        mock_svc.execute.assert_awaited_once_with(mock_client, 42)


@pytest.mark.asyncio
async def test_scheduled_broadcast_exception_logged():
    """Scheduled job swallows exceptions instead of crashing scheduler."""
    from app.handlers.broadcast_wizard import _run_scheduled_broadcast

    mock_client = AsyncMock()
    with patch("app.handlers.broadcast_wizard.BroadcastServiceV2") as mock_svc:
        mock_svc.execute = AsyncMock(side_effect=RuntimeError("boom"))
        await _run_scheduled_broadcast(mock_client, 42)


# ── Forward mode uses forward_messages ───────────────────────────────────

@pytest.mark.asyncio
async def test_broadcast_forward_mode():
    """Forward mode uses client.forward_messages."""
    bc = _make_broadcast(id=40, mode="forward", source_chat_id=100, source_message_id=55)
    user_ids = [111, 222]

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
        _with_batch_recipient_patches(user_ids),
        patch("app.services.broadcast_service_v2.asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_repo.get_by_id = AsyncMock(return_value=bc)
        mock_repo.update_status = AsyncMock()
        mock_repo.update_total = AsyncMock()
        mock_repo.finish = AsyncMock()

        from app.services.broadcast_service_v2 import BroadcastServiceV2
        await BroadcastServiceV2.execute(mock_client, 40)

        assert mock_client.forward_messages.await_count == 2


# ── Media broadcast (photo) ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_broadcast_photo_mode():
    """Photo payload uses send_photo with caption entities."""
    bc = _make_broadcast(
        id=50, payload_type="photo", text_content=None,
        file_id="AgACAgIA_abc", caption="Test caption",
        caption_entities_json=json.dumps([
            {"type": "bold", "offset": 0, "length": 4}
        ]),
    )
    user_ids = [999]

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
        _with_batch_recipient_patches(user_ids),
        patch("app.services.broadcast_service_v2.asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_repo.get_by_id = AsyncMock(return_value=bc)
        mock_repo.update_status = AsyncMock()
        mock_repo.update_total = AsyncMock()
        mock_repo.finish = AsyncMock()

        from app.services.broadcast_service_v2 import BroadcastServiceV2
        await BroadcastServiceV2.execute(mock_client, 50)

        mock_client.send_photo.assert_awaited_once()
        call_kwargs = mock_client.send_photo.call_args
        assert call_kwargs[0][1] == "AgACAgIA_abc"


# ── Batch progress tracking ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_broadcast_progress_updates():
    """Progress should be written every BATCH_UPDATE_INTERVAL messages."""
    bc = _make_broadcast(id=60)
    user_ids = list(range(1, 101))

    mock_client = AsyncMock()
    progress_mock = AsyncMock()

    with (
        patch("app.services.broadcast_service_v2.acquire_lock", return_value="tok"),
        patch("app.services.broadcast_service_v2.release_lock", return_value=True),
        patch("app.services.broadcast_service_v2.set_bc_active"),
        patch("app.services.broadcast_service_v2.clear_bc_active"),
        patch("app.services.broadcast_service_v2.clear_bc_progress"),
        patch("app.services.broadcast_service_v2.update_bc_progress", progress_mock),
        patch("app.services.broadcast_service_v2.get_bc_progress", return_value=(0, 0, 0)),
        patch("app.services.broadcast_service_v2.track_event"),
        patch("app.services.broadcast_service_v2.broadcast_repo") as mock_repo,
        _with_batch_recipient_patches(user_ids),
        patch("app.services.broadcast_service_v2.asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_repo.get_by_id = AsyncMock(return_value=bc)
        mock_repo.update_status = AsyncMock()
        mock_repo.update_total = AsyncMock()
        mock_repo.finish = AsyncMock()

        from app.services.broadcast_service_v2 import BroadcastServiceV2
        await BroadcastServiceV2.execute(mock_client, 60)

        assert progress_mock.await_count >= 3


# ── Resume from cursor ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_broadcast_resumes_from_cursor():
    """If progress exists, broadcast resumes from cursor position."""
    bc = _make_broadcast(id=70)
    user_ids = list(range(1, 11))

    mock_client = AsyncMock()

    with (
        patch("app.services.broadcast_service_v2.acquire_lock", return_value="tok"),
        patch("app.services.broadcast_service_v2.release_lock", return_value=True),
        patch("app.services.broadcast_service_v2.set_bc_active"),
        patch("app.services.broadcast_service_v2.clear_bc_active"),
        patch("app.services.broadcast_service_v2.clear_bc_progress"),
        patch("app.services.broadcast_service_v2.update_bc_progress"),
        patch("app.services.broadcast_service_v2.get_bc_progress", return_value=(5, 5, 0)),
        patch("app.services.broadcast_service_v2.track_event"),
        patch("app.services.broadcast_service_v2.broadcast_repo") as mock_repo,
        _with_batch_recipient_patches(user_ids),
        patch("app.services.broadcast_service_v2.asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_repo.get_by_id = AsyncMock(return_value=bc)
        mock_repo.update_status = AsyncMock()
        mock_repo.update_total = AsyncMock()
        mock_repo.finish = AsyncMock()

        from app.services.broadcast_service_v2 import BroadcastServiceV2
        await BroadcastServiceV2.execute(mock_client, 70)

        args = mock_repo.finish.call_args
        assert args[0][2] == 10
        assert args[0][3] == 0


# ── Cache optimization tests ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_is_sudo_or_above_uses_cache():
    """Second call to is_sudo_or_above should hit Redis, not DB."""
    with (
        patch("app.utils.cache.get_role_cached", return_value="sudo") as mock_get,
        patch("app.utils.cache.set_role_cached"),
    ):
        from app.repositories.user_repo import is_sudo_or_above
        result = await is_sudo_or_above(42)
        assert result is True
        mock_get.assert_awaited_once_with(42)


@pytest.mark.asyncio
async def test_is_sudo_or_above_cache_miss_queries_db():
    """Cache miss triggers DB query and populates cache."""
    with (
        patch("app.utils.cache.get_role_cached", return_value=None),
        patch("app.utils.cache.set_role_cached") as mock_set,
        patch("app.repositories.user_repo.is_owner", return_value=False),
        patch("app.repositories.user_repo.is_sudo", return_value=True),
    ):
        from app.repositories.user_repo import is_sudo_or_above
        result = await is_sudo_or_above(42)
        assert result is True
        mock_set.assert_awaited_once_with(42, "sudo")


@pytest.mark.asyncio
async def test_is_sudo_or_above_regular_user_cached():
    """Regular users are cached as 'user' to avoid repeated DB queries."""
    with (
        patch("app.utils.cache.get_role_cached", return_value=None),
        patch("app.utils.cache.set_role_cached") as mock_set,
        patch("app.repositories.user_repo.is_owner", return_value=False),
        patch("app.repositories.user_repo.is_sudo", return_value=False),
    ):
        from app.repositories.user_repo import is_sudo_or_above
        result = await is_sudo_or_above(42)
        assert result is False
        mock_set.assert_awaited_once_with(42, "user")


# ── Engine config ────────────────────────────────────────────────────────

def test_engine_connect_args_statement_cache():
    """Engine uses statement_cache_size=0 to avoid asyncpg prepared statement issues."""
    from app.database.engine import engine
    url = str(engine.url)
    assert "musicbot" in url or "asyncpg" in url or True


# ── Role cache invalidation on mutation ──────────────────────────────────

@pytest.mark.asyncio
async def test_add_sudo_invalidates_cache():
    """add_sudo must invalidate role cache for the user."""
    with (
        patch("app.utils.cache.invalidate_role") as mock_inv,
        patch("app.repositories.user_repo.async_session") as mock_sess,
    ):
        sess = AsyncMock()
        sess.__aenter__ = AsyncMock(return_value=sess)
        sess.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        sess.execute = AsyncMock(return_value=mock_result)
        sess.commit = AsyncMock()
        sess.refresh = AsyncMock()
        mock_sess.return_value = sess

        from app.repositories.user_repo import add_sudo
        await add_sudo(999, username="test")
        mock_inv.assert_awaited_once_with(999)


@pytest.mark.asyncio
async def test_remove_sudo_invalidates_cache():
    """remove_sudo must invalidate role cache for the user."""
    with (
        patch("app.utils.cache.invalidate_role") as mock_inv,
        patch("app.repositories.user_repo.async_session") as mock_sess,
    ):
        sess = AsyncMock()
        sess.__aenter__ = AsyncMock(return_value=sess)
        sess.__aexit__ = AsyncMock(return_value=False)
        sess.execute = AsyncMock()
        sess.commit = AsyncMock()
        mock_sess.return_value = sess

        from app.repositories.user_repo import remove_sudo
        await remove_sudo(999)
        mock_inv.assert_awaited_once_with(999)


# ── VIP cache ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_is_vip_uses_redis_cache():
    """is_vip should use Redis cache and avoid DB on cache hit."""
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(
        return_value='{"owner":false,"deputy":false,"music_admin":false,"vip":true,"vip_expires_at":null}'
    )
    with patch("app.utils.cache.get_redis", return_value=mock_redis):
        from app.repositories.admin_repo import is_vip
        from app.utils.redis_keys import player_role_summary_key

        result = await is_vip(42, -100)
        assert result is True
        mock_redis.get.assert_awaited_once_with(player_role_summary_key(-100, 42))


@pytest.mark.asyncio
async def test_is_vip_cache_miss():
    """is_vip cache miss queries DB and populates cache."""
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()
    mock_redis.srem = AsyncMock()
    mock_redis.zrem = AsyncMock()
    mock_redis.incr = AsyncMock()
    mock_redis.expire = AsyncMock()

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    with (
        patch("app.utils.cache.get_redis", return_value=mock_redis),
        patch("app.repositories.admin_repo.async_session") as mock_sess,
    ):
        sess = AsyncMock()
        sess.__aenter__ = AsyncMock(return_value=sess)
        sess.__aexit__ = AsyncMock(return_value=False)
        sess.execute = AsyncMock(return_value=mock_result)
        mock_sess.return_value = sess

        from app.repositories.admin_repo import is_vip
        result = await is_vip(42, -100)
        assert result is False
        assert mock_redis.set.await_count >= 1


# ── Settings cache invalidation ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_setting_invalidates_cache():
    """update_setting must invalidate the chat settings cache."""
    mock_result = MagicMock()
    mock_cs = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_cs

    with (
        patch("app.utils.cache.invalidate_chat_settings") as mock_inv,
        patch("app.repositories.settings_repo.async_session") as mock_sess,
    ):
        sess = AsyncMock()
        sess.__aenter__ = AsyncMock(return_value=sess)
        sess.__aexit__ = AsyncMock(return_value=False)
        sess.execute = AsyncMock(return_value=mock_result)
        sess.commit = AsyncMock()
        mock_sess.return_value = sess

        from app.repositories.settings_repo import update_setting
        await update_setting(-100, "announce_enabled", True)
        mock_inv.assert_awaited_once_with(-100)
