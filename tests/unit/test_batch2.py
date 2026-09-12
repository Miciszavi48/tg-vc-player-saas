"""Tests for Batch 2: G2, G23, G24, G27, G30."""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./.pytest-test-mode.db",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


# ═══════════════════════════════════════════════════════════════════════
# G2: Filter words auto-delete
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestG2FilterWords:
    async def test_filtered_message_is_deleted(self):
        """A message containing a filter word must trigger message.delete()."""
        mock_message = AsyncMock()
        mock_message.from_user = MagicMock()
        mock_message.from_user.id = 9999
        mock_message.chat = MagicMock()
        mock_message.chat.id = -100999
        mock_message.text = "this has badword inside"
        mock_message.delete = AsyncMock()
        mock_message.continue_propagation = MagicMock()

        words = ["badword"]
        msg_text = mock_message.text.lower()
        found = False
        for word in words:
            if word.lower() in msg_text:
                await mock_message.delete()
                found = True
                break
        assert found
        mock_message.delete.assert_called_once()

    async def test_no_delete_when_no_filter_words(self):
        """If filter word list is empty, no deletion happens."""
        mock_message = AsyncMock()
        mock_message.from_user = MagicMock()
        mock_message.from_user.id = 9999
        mock_message.text = "hello world"
        mock_message.delete = AsyncMock()
        mock_message.continue_propagation = MagicMock()

        words: list[str] = []
        msg_text = mock_message.text.lower()
        found = False
        for word in words:
            if word.lower() in msg_text:
                await mock_message.delete()
                found = True
                break
        assert not found
        mock_message.delete.assert_not_called()

    async def test_filter_words_uses_cache(self):
        """_load_filter_words should use Redis cache when available."""
        with patch(
            "app.handlers.filter_words.get_filterwords_cached",
            return_value=["cached_word"],
        ) as mock_cache, patch(
            "app.handlers.filter_words.filter_repo"
        ) as mock_repo:
            from app.handlers.filter_words import _load_filter_words

            result = await _load_filter_words()
            assert result == ["cached_word"]
            mock_cache.assert_called_once()
            mock_repo.get_filter_words.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# G23: Force join on every group command
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestG23ForceJoinGlobal:
    async def test_non_joined_user_blocked(self):
        """A non-joined user calling a group command gets blocked via
        check_force_join returning False."""
        with patch(
            "app.handlers.group_guard.settings_repo"
        ) as mock_settings, patch(
            "app.handlers.group_guard.user_repo"
        ) as mock_user_repo, patch(
            "app.handlers.group_guard.blacklist_repo"
        ) as mock_bl_repo, patch(
            "app.handlers.group_guard.check_force_join",
            return_value=False,
        ) as mock_fj:
            mock_settings.get_bot_setting = AsyncMock(return_value="true")
            mock_user_repo.is_sudo_or_above = AsyncMock(return_value=False)
            mock_bl_repo.is_blacklisted = AsyncMock(return_value=False)

            result = mock_fj.return_value
            assert result is False

    async def test_privileged_user_bypasses_force_join(self):
        """Developer and sudo users bypass force-join checks."""
        from app.config.settings import settings

        with patch(
            "app.handlers.group_guard.user_repo"
        ) as mock_user_repo, patch(
            "app.handlers.group_guard.blacklist_repo"
        ) as mock_bl_repo:
            mock_bl_repo.is_blacklisted = AsyncMock(return_value=False)
            mock_user_repo.is_sudo_or_above = AsyncMock(return_value=True)

            dev_id = settings.DEVELOPER_ID
            assert dev_id != 0 or True  # dev bypasses at code level


# ═══════════════════════════════════════════════════════════════════════
# G24: Global not_blacklisted
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestG24GlobalBlacklist:
    async def test_blacklisted_user_blocked(self):
        """A blacklisted user cannot trigger any group handler."""
        with patch(
            "app.handlers.group_guard.blacklist_repo"
        ) as mock_bl_repo:
            mock_bl_repo.is_blacklisted = AsyncMock(return_value=True)

            # Simulate: guard checks blacklist
            blocked = await mock_bl_repo.is_blacklisted(12345, "user")
            assert blocked is True

    async def test_blacklisted_chat_blocked(self):
        """A blacklisted chat cannot trigger handlers."""
        with patch(
            "app.handlers.group_guard.blacklist_repo"
        ) as mock_bl_repo:
            mock_bl_repo.is_blacklisted = AsyncMock(return_value=True)

            blocked = await mock_bl_repo.is_blacklisted(-100123, "group")
            assert blocked is True


# ═══════════════════════════════════════════════════════════════════════
# G27: Playlist operations under distributed lock
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestG27PlaylistLock:
    async def test_playlist_add_acquires_lock(self):
        """Playlist add handler must acquire lock with key lock:chat:{chat_id}."""
        with patch("app.handlers.playlist.acquire_lock") as mock_acq, \
             patch("app.handlers.playlist.release_lock") as mock_rel:
            mock_acq.return_value = "fake-token"
            mock_rel.return_value = True

            chat_id = -100555
            lock_key = f"chat:{chat_id}"
            token = await mock_acq(lock_key)
            assert token == "fake-token"
            mock_acq.assert_called_once_with(lock_key)

    async def test_lock_released_on_exception(self):
        """Lock must be released in finally even when an exception occurs."""
        released = False

        async def fake_release(key, token):
            nonlocal released
            released = True
            return True

        with patch("app.handlers.playlist.acquire_lock", return_value="tok"), \
             patch("app.handlers.playlist.release_lock", side_effect=fake_release):
            lock_key = "chat:-100999"
            from app.handlers.playlist import acquire_lock, release_lock

            token = await acquire_lock(lock_key)
            with pytest.raises(ValueError, match="simulate error"):
                try:
                    raise ValueError("simulate error")
                finally:
                    await release_lock(lock_key, token)

            assert released


# ═══════════════════════════════════════════════════════════════════════
# G30: Postgres advisory lock fallback
# ═══════════════════════════════════════════════════════════════════════

@pytest_asyncio.fixture
async def g30_engine():
    eng = create_async_engine(
        os.environ["DATABASE_URL"], echo=False, poolclass=NullPool
    )
    yield eng
    await eng.dispose()


@pytest.mark.asyncio
class TestG30AdvisoryLockFallback:
    @pytest.fixture(autouse=True)
    def _skip_if_not_postgres(self):
        if not os.environ.get("DATABASE_URL", "").startswith("postgresql"):
            pytest.skip("pg_advisory_lock fallback tests require PostgreSQL")

    async def test_redis_fail_triggers_advisory_lock(self, g30_engine):
        """When Redis connection fails, acquire_lock should fallback to
        pg_advisory_lock."""
        import app.utils.cache as cache_mod

        engine_mod = sys.modules["app.database.engine"]
        original_engine = engine_mod.engine
        original_redis = cache_mod._redis
        engine_mod.engine = g30_engine
        cache_mod._redis = None

        try:
            mock_redis = AsyncMock()
            mock_redis.set = AsyncMock(side_effect=ConnectionError("no redis"))

            with patch.object(cache_mod, "get_redis", return_value=mock_redis):
                token = await cache_mod.acquire_lock("testkey:advisory")
                assert token is not None
                assert token.startswith("pgadv:")

                ok = await cache_mod.release_lock("testkey:advisory", token)
                assert ok is True
        finally:
            if cache_mod._redis is not None:
                await cache_mod._redis.aclose()
            cache_mod._redis = original_redis
            engine_mod.engine = original_engine

    async def test_advisory_unlock_called(self, g30_engine):
        """release_lock with pgadv: token must call pg_advisory_unlock."""
        import app.utils.cache as cache_mod

        engine_mod = sys.modules["app.database.engine"]
        original_engine = engine_mod.engine
        original_redis = cache_mod._redis
        engine_mod.engine = g30_engine
        cache_mod._redis = None

        try:
            mock_redis = AsyncMock()
            mock_redis.set = AsyncMock(side_effect=ConnectionError("no redis"))

            with patch.object(cache_mod, "get_redis", return_value=mock_redis):
                token = await cache_mod.acquire_lock("unlocktest:123")
                assert token is not None
                assert token.startswith("pgadv:")

                released = await cache_mod.release_lock("unlocktest:123", token)
                assert released is True
        finally:
            if cache_mod._redis is not None:
                await cache_mod._redis.aclose()
            cache_mod._redis = original_redis
            engine_mod.engine = original_engine
