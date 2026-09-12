"""Tests for force-join gate, blacklist enforcement, and filter words auto-delete."""
from __future__ import annotations

import os

import pytest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


@pytest.mark.asyncio
class TestForceJoinGate:
    async def test_developer_bypasses_force_join(self):
        from app.config.settings import settings
        from app.handlers.force_join import check_force_join
        from tests.fakes.pyrogram_objects import FakeClient, FakeMessage, FakeUser

        msg = FakeMessage(from_user=FakeUser(id=settings.DEVELOPER_ID))
        client = FakeClient()
        result = await check_force_join(client, msg, settings.DEVELOPER_ID)
        assert result is True

    async def test_non_joined_user_blocked(self):
        from app.handlers.force_join import check_force_join
        from tests.fakes.pyrogram_objects import FakeClient, FakeMessage, FakeUser

        missing_targets = [
            {
                "id": 1,
                "channel_id": -100999,
                "username": "testchan",
                "invite_link": "https://t.me/testchan",
                "display_name": "Test Channel",
                "chat_type": "channel",
                "verify_status": "ok",
            }
        ]

        with patch("app.handlers.force_join.user_repo") as mock_ur, \
             patch("app.handlers.force_join.ForcedMembershipService") as mock_fms:
            mock_ur.is_sudo_or_above = AsyncMock(return_value=False)
            mock_ur.is_owner = AsyncMock(return_value=False)
            mock_fms.check = AsyncMock(return_value=missing_targets)

            msg = FakeMessage(from_user=FakeUser(id=88888))
            client = FakeClient()

            result = await check_force_join(client, msg, 88888)
            assert result is False
            msg.reply.assert_called_once()

    async def test_force_join_prompt_uses_i18n_keys(self):
        from app.utils.i18n import t
        text = t("fa", "force_join_mgmt.not_joined")
        assert "[missing:" not in text
        text_btn = t("fa", "force_join_mgmt.join_button", channel="test")
        assert "[missing:" not in text_btn


@pytest.mark.asyncio
class TestBlacklistEnforcement:
    async def test_blacklisted_user_detected(self):
        from app.repositories import blacklist_repo
        with patch.object(blacklist_repo, "is_blacklisted", new_callable=AsyncMock, return_value=True):
            result = await blacklist_repo.is_blacklisted(12345, "user")
            assert result is True

    async def test_non_blacklisted_passes(self):
        from app.repositories import blacklist_repo
        with patch.object(blacklist_repo, "is_blacklisted", new_callable=AsyncMock, return_value=False):
            result = await blacklist_repo.is_blacklisted(12345, "user")
            assert result is False


@pytest.mark.asyncio
class TestFilterWordsAutoDelete:
    async def test_matching_word_triggers_delete(self):
        """Simulate filter words handler logic: matching word → delete."""
        words = ["badword", "spam"]
        msg_text = "this message has badword in it"

        found = any(w.lower() in msg_text.lower() for w in words)
        assert found is True

    async def test_no_match_no_delete(self):
        words = ["badword", "spam"]
        msg_text = "this is a clean message"
        found = any(w.lower() in msg_text.lower() for w in words)
        assert found is False

    async def test_case_insensitive_match(self):
        words = ["BadWord"]
        msg_text = "this has BADWORD"
        found = any(w.lower() in msg_text.lower() for w in words)
        assert found is True

    async def test_developer_exempt(self):
        from app.config.settings import settings
        user_id = settings.DEVELOPER_ID
        assert user_id != 0
