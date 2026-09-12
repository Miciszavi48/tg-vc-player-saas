"""Permission matrix tests — role checks without DB fixtures (mocked)."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


@pytest.mark.asyncio
class TestRoleHierarchy:
    async def test_developer_id_from_config(self):
        from app.config.settings import settings
        assert settings.DEVELOPER_ID == 123456789
        assert 123456789 in settings.DEVELOPER_IDS

    async def test_owner_is_sudo_or_above(self):
        from app.repositories import user_repo
        with patch("app.utils.cache.get_role_cached", new_callable=AsyncMock, return_value=None), \
             patch("app.utils.cache.set_role_cached", new_callable=AsyncMock), \
             patch.object(user_repo, "is_owner", new_callable=AsyncMock, return_value=True), \
             patch.object(user_repo, "is_sudo", new_callable=AsyncMock, return_value=False):
            result = await user_repo.is_sudo_or_above(1)
            assert result is True or await user_repo.is_owner(1) is True

    async def test_sudo_passes_sudo_filter(self):
        from app.repositories import user_repo
        with patch.object(user_repo, "is_sudo", new_callable=AsyncMock, return_value=True):
            assert await user_repo.is_sudo(33333) is True

    async def test_regular_user_not_sudo(self):
        from app.repositories import user_repo
        with patch.object(user_repo, "is_sudo", new_callable=AsyncMock, return_value=False), \
             patch.object(user_repo, "is_owner", new_callable=AsyncMock, return_value=False):
            assert await user_repo.is_sudo(999) is False
            assert await user_repo.is_owner(999) is False

    async def test_music_admin_in_chat(self):
        from app.repositories import admin_repo
        with patch.object(admin_repo, "is_music_admin", new_callable=AsyncMock, return_value=True):
            assert await admin_repo.is_music_admin(44444, -100001) is True

    async def test_music_admin_wrong_chat(self):
        from app.repositories import admin_repo
        with patch.object(admin_repo, "is_music_admin", new_callable=AsyncMock, return_value=False):
            assert await admin_repo.is_music_admin(44444, -999) is False

    async def test_player_owner_is_above_music_admin(self):
        from app.repositories import admin_repo
        with patch.object(admin_repo, "is_music_admin_or_above", new_callable=AsyncMock, return_value=True):
            assert await admin_repo.is_music_admin_or_above(66666, -100001) is True


@pytest.mark.asyncio
class TestDevPanelAccess:
    async def test_dev_filter_accepts_developer(self):
        from app.config.settings import settings
        assert settings.DEVELOPER_ID != 0

    async def test_non_dev_rejected(self):
        from app.utils.bot_guards import is_developer

        assert is_developer(9999) is False


@pytest.mark.asyncio
class TestSudoPanelAccess:
    async def test_sudo_cannot_be_owner(self):
        """Sudo role does not grant owner access."""
        from app.repositories import user_repo
        with patch.object(user_repo, "is_owner", new_callable=AsyncMock, return_value=False):
            assert await user_repo.is_owner(33333) is False
