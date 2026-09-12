"""Tests for comma-separated DEVELOPER_ID env and multi-developer permissions."""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

from app.config.settings import _parse_developer_id_env
from app.repositories.global_ban_repo import (
    GlobalBanValidationError,
    validate_global_ban_user_id,
)

PRIMARY = 123456789
SECOND = 987654321
NON_DEV = 999999001


class TestParseDeveloperIdEnv:
    def test_single_value(self):
        ids, primary = _parse_developer_id_env("123456789")
        assert primary == 123456789
        assert ids == frozenset({123456789})

    def test_multi_value(self):
        ids, primary = _parse_developer_id_env("123456789,987654321")
        assert primary == 123456789
        assert ids == frozenset({123456789, 987654321})

    def test_whitespace_stripped(self):
        ids, primary = _parse_developer_id_env(" 123456789 , 987654321 ")
        assert primary == 123456789
        assert ids == frozenset({123456789, 987654321})

    def test_zero_is_empty(self):
        ids, primary = _parse_developer_id_env("0")
        assert primary == 0
        assert ids == frozenset()

    def test_empty_is_empty(self):
        ids, primary = _parse_developer_id_env("")
        assert primary == 0
        assert ids == frozenset()

    def test_missing_is_empty(self, monkeypatch):
        monkeypatch.delenv("DEVELOPER_ID", raising=False)
        ids, primary = _parse_developer_id_env(None)
        assert primary == 0
        assert ids == frozenset()

    @pytest.mark.parametrize(
        "raw",
        ["abc", "123,,456", "123,abc"],
    )
    def test_invalid_syntax_raises(self, raw: str):
        with pytest.raises(ValueError):
            _parse_developer_id_env(raw)


class TestSettingsSingletonSingleDev:
    def test_conftest_primary_and_set(self):
        from app.config.settings import settings

        assert settings.DEVELOPER_ID == PRIMARY
        assert settings.DEVELOPER_IDS == frozenset({PRIMARY})


def _patch_developer_settings(monkeypatch, ids: frozenset[int], primary: int) -> None:
    from app.config import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "DEVELOPER_IDS", ids)
    monkeypatch.setattr(settings_mod.settings, "DEVELOPER_ID", primary)


class TestIsDeveloper:
    def test_primary_is_developer(self, monkeypatch):
        from app.utils.bot_guards import is_developer

        _patch_developer_settings(
            monkeypatch, frozenset({PRIMARY, SECOND}), PRIMARY
        )
        assert is_developer(PRIMARY) is True

    def test_second_is_developer(self, monkeypatch):
        from app.utils.bot_guards import is_developer

        _patch_developer_settings(
            monkeypatch, frozenset({PRIMARY, SECOND}), PRIMARY
        )
        assert is_developer(SECOND) is True

    def test_non_dev_false(self, monkeypatch):
        from app.utils.bot_guards import is_developer

        _patch_developer_settings(
            monkeypatch, frozenset({PRIMARY, SECOND}), PRIMARY
        )
        assert is_developer(NON_DEV) is False
        assert is_developer(None) is False


@pytest.mark.asyncio
class TestDevFilterAndDeveloperOnly:
    async def test_dev_filter_allows_second_developer(self, monkeypatch):
        """dev_filter() delegates to is_developer(user.id)."""
        from app.utils.bot_guards import is_developer

        _patch_developer_settings(
            monkeypatch, frozenset({PRIMARY, SECOND}), PRIMARY
        )
        assert is_developer(SECOND) is True

    async def test_developer_only_allows_second_developer(self, monkeypatch):
        from app.utils.decorators import developer_only

        _patch_developer_settings(
            monkeypatch, frozenset({PRIMARY, SECOND}), PRIMARY
        )

        @developer_only
        async def handler(client, update):
            return "ok"

        update = SimpleNamespace(
            from_user=SimpleNamespace(id=SECOND),
            answer=AsyncMock(),
        )
        result = await handler(None, update)
        assert result == "ok"


class TestGlobalBanValidation:
    def test_cannot_ban_primary(self, monkeypatch):
        _patch_developer_settings(
            monkeypatch, frozenset({PRIMARY, SECOND}), PRIMARY
        )
        with pytest.raises(GlobalBanValidationError):
            validate_global_ban_user_id(PRIMARY)

    def test_cannot_ban_second(self, monkeypatch):
        _patch_developer_settings(
            monkeypatch, frozenset({PRIMARY, SECOND}), PRIMARY
        )
        with pytest.raises(GlobalBanValidationError):
            validate_global_ban_user_id(SECOND)

    def test_can_ban_non_dev(self, monkeypatch):
        _patch_developer_settings(
            monkeypatch, frozenset({PRIMARY, SECOND}), PRIMARY
        )
        validate_global_ban_user_id(NON_DEV)


@pytest.mark.asyncio
class TestBootstrapMultiDeveloper:
    async def test_seeds_all_developer_ids(self, monkeypatch):
        from app.bootstrap import bootstrap

        _patch_developer_settings(
            monkeypatch, frozenset({PRIMARY, SECOND}), PRIMARY
        )

        added: list[object] = []

        class _FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            def begin(self):
                return self

            async def execute(self, _stmt):
                result = MagicMock()
                result.scalar_one_or_none.return_value = None
                return result

            def add(self, obj):
                added.append(obj)

        with patch("app.bootstrap.async_session", return_value=_FakeSession()):
            await bootstrap()

        user_ids = {getattr(o, "user_id", None) for o in added}
        assert PRIMARY in user_ids
        assert SECOND in user_ids


@pytest.mark.asyncio
class TestPermissionsMatrixSecondDev:
    async def test_second_dev_in_set(self, monkeypatch):
        from app.config.settings import settings

        _patch_developer_settings(
            monkeypatch, frozenset({PRIMARY, SECOND}), PRIMARY
        )
        assert SECOND in settings.DEVELOPER_IDS
        assert settings.DEVELOPER_ID == PRIMARY

    async def test_non_dev_not_in_set(self, monkeypatch):
        from app.utils.bot_guards import is_developer

        _patch_developer_settings(
            monkeypatch, frozenset({PRIMARY, SECOND}), PRIMARY
        )
        assert is_developer(9999) is False
