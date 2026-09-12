"""Tests for Developer Panel bot update / reload diagnostics."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

if "pyromod" not in sys.modules:
    pyromod_module = ModuleType("pyromod")
    pyromod_exceptions = ModuleType("pyromod.exceptions")
    pyromod_exceptions.ListenerStopped = Exception
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from app.config.settings import settings
from app.handlers import dev_panel
from app.services.bot_update_service import (
    BotDiagnosticsSnapshot,
    ReloadConfigStatus,
    ReloadResult,
    build_diagnostics_snapshot,
    execute_configured_reload,
    format_bot_update_panel,
    get_reload_config_status,
    mark_process_started,
    reload_is_available,
)
from app.utils.i18n import t
from app.utils.ui import CB, KeyboardFactory


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator

    def __getattr__(self, name: str):
        if name.startswith("on_"):

            def _register_other(*args, **kwargs):  # noqa: ANN001, ANN002
                def _decorator(fn):
                    return fn

                return _decorator

            return _register_other
        raise AttributeError(name)


def _handler_by_name(handlers: list, name: str):
    for fn in handlers:
        if fn.__name__ == name:
            return fn
    raise AssertionError(f"handler not found: {name}")


def _kb_callbacks(kb) -> set[str]:
    return {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data
    }


def _pm_query(user_id: int, data: str):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="Tester"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
    )


def _sample_snapshot(**overrides) -> BotDiagnosticsSnapshot:
    base = BotDiagnosticsSnapshot(
        python_version="3.12.0",
        platform_system="Windows",
        process_uptime_seconds=3600,
        db_ok=True,
        redis_ok=True,
        telegram_ok=True,
        scheduler_running=True,
        pytgcalls_available=True,
        pytgcalls_started=True,
        active_calls=2,
        helpers_total=3,
        helpers_active=2,
        memory_mb=128.5,
        cpu_percent=12.3,
        bot_enabled=True,
        config_presence={"bot_token_set": True},
        reload_config=ReloadConfigStatus(state="not_configured", detail="none"),
        error_metrics={},
        errors_available=False,
        ytdlp_available=True,
        ffmpeg_available=True,
    )
    return BotDiagnosticsSnapshot(**{**base.__dict__, **overrides})


@pytest.mark.asyncio
async def test_dev_settings_contains_bot_update_button():
    kb = KeyboardFactory.dev_sub_settings("en")
    assert CB["DEV_BOT_UPDATE"] in _kb_callbacks(kb)


@pytest.mark.asyncio
async def test_developer_can_open_bot_update_panel():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_bot_update")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_BOT_UPDATE"])

    with patch(
        "app.handlers.dev_panel.build_diagnostics_snapshot",
        AsyncMock(return_value=_sample_snapshot()),
    ):
        await handler(SimpleNamespace(), query)

    body = query.message.edit_text.await_args.args[0]
    assert t("fa", "bot_update.title") in body or t("en", "bot_update.title") in body
    assert settings.BOT_TOKEN not in body


@pytest.mark.asyncio
async def test_non_developer_cannot_open_bot_update_panel():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_bot_update")
    query = _pm_query(9999, CB["DEV_BOT_UPDATE"])

    with patch(
        "app.handlers.dev_panel.build_diagnostics_snapshot",
        AsyncMock(),
    ) as build_mock:
        result = await handler(SimpleNamespace(), query)

    assert result is None
    build_mock.assert_not_awaited()


def test_panel_includes_runtime_and_connectivity_fields():
    text = format_bot_update_panel("en", _sample_snapshot())
    assert "3.12.0" in text
    assert "DB:" in text
    assert "Redis:" in text


@pytest.mark.asyncio
async def test_diagnostics_failures_do_not_crash():
    with (
        patch("app.services.bot_update_service._ping_database", AsyncMock(return_value=False)),
        patch("app.services.bot_update_service._ping_redis", AsyncMock(return_value=False)),
        patch("app.services.bot_update_service._ping_telegram", AsyncMock(return_value=False)),
        patch("app.services.bot_update_service._helper_counts", AsyncMock(return_value=(0, 0))),
        patch("app.services.bot_update_service._system_metrics", AsyncMock(return_value=(None, None))),
        patch("app.services.bot_update_service._media_tool_availability", AsyncMock(return_value=(None, None))),
        patch("app.services.bot_update_service._error_metrics_summary", AsyncMock(return_value=({}, False))),
        patch(
            "app.repositories.settings_repo.get_bot_setting_bool",
            AsyncMock(return_value=True),
        ),
    ):
        snap = await build_diagnostics_snapshot()
    assert snap.db_ok is False
    assert snap.redis_ok is False


def test_reload_not_configured_by_default(monkeypatch):
    monkeypatch.setattr(settings, "BOT_RELOAD_SENTINEL_PATH", "")
    monkeypatch.setattr(settings, "BOT_RELOAD_COMMAND", "")
    monkeypatch.setattr(settings, "BOT_RELOAD_ENABLED", True)
    assert get_reload_config_status().state == "not_configured"
    assert reload_is_available() is False


@pytest.mark.asyncio
async def test_reload_prompt_shows_not_configured_alert():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_bot_update_reload_prompt")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_BOT_UPDATE_RELOAD"])

    with patch("app.handlers.dev_panel.reload_is_available", return_value=False):
        await handler(SimpleNamespace(), query)

    query.answer.assert_awaited()


@pytest.mark.asyncio
async def test_reload_prompt_shows_confirmation_when_configured():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_bot_update_reload_prompt")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_BOT_UPDATE_RELOAD"])

    with patch("app.handlers.dev_panel.reload_is_available", return_value=True):
        await handler(SimpleNamespace(), query)

    kb = query.message.edit_text.await_args.kwargs["reply_markup"]
    callbacks = _kb_callbacks(kb)
    assert any(cb.startswith(CB["DEV_BOT_UPDATE_RELOAD_EXEC_PREFIX"]) for cb in callbacks)


@pytest.mark.asyncio
async def test_confirm_executes_reload_for_same_developer(tmp_path, monkeypatch):
    sentinel = tmp_path / "reload.request"
    monkeypatch.setattr(settings, "BOT_RELOAD_SENTINEL_PATH", str(sentinel))
    monkeypatch.setattr(settings, "BOT_RELOAD_COMMAND", "")
    monkeypatch.setattr(settings, "BOT_RELOAD_ENABLED", True)

    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_bot_update_reload_confirm")
    token = f"{settings.DEVELOPER_ID}:{int(time.time())}"
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_BOT_UPDATE_RELOAD_EXEC_PREFIX']}{token}")

    await handler(SimpleNamespace(), query)
    assert sentinel.exists()


@pytest.mark.asyncio
async def test_wrong_user_confirm_rejected():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_bot_update_reload_confirm")
    token = f"{settings.DEVELOPER_ID}:{int(time.time())}"
    query = _pm_query(8888, f"{CB['DEV_BOT_UPDATE_RELOAD_EXEC_PREFIX']}{token}")

    with patch(
        "app.handlers.dev_panel.execute_configured_reload",
        AsyncMock(),
    ) as exec_mock:
        await handler(SimpleNamespace(), query)

    exec_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_confirm_rejected():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_bot_update_reload_confirm")
    token = f"{settings.DEVELOPER_ID}:{int(time.time()) - 99999}"
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_BOT_UPDATE_RELOAD_EXEC_PREFIX']}{token}")

    with patch(
        "app.handlers.dev_panel.execute_configured_reload",
        AsyncMock(),
    ) as exec_mock:
        await handler(SimpleNamespace(), query)

    exec_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_malformed_confirm_rejected():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_bot_update_reload_malformed")
    query = _pm_query(settings.DEVELOPER_ID, "dev:bot_update:reload:do:bad")

    await handler(SimpleNamespace(), query)
    query.answer.assert_awaited()


@pytest.mark.asyncio
async def test_cancel_does_not_execute_reload(tmp_path, monkeypatch):
    sentinel = tmp_path / "reload.request"
    monkeypatch.setattr(settings, "BOT_RELOAD_SENTINEL_PATH", str(sentinel))
    monkeypatch.setattr(settings, "BOT_RELOAD_COMMAND", "")

    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_bot_update_reload_cancel")
    token = f"{settings.DEVELOPER_ID}:{int(time.time())}"
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_BOT_UPDATE_RELOAD_ABORT_PREFIX']}{token}")

    with patch(
        "app.handlers.dev_panel.build_diagnostics_snapshot",
        AsyncMock(return_value=_sample_snapshot()),
    ):
        await handler(SimpleNamespace(), query)

    assert not sentinel.exists()


@pytest.mark.asyncio
async def test_command_failure_reported_safely(monkeypatch):
    monkeypatch.setattr(settings, "BOT_RELOAD_SENTINEL_PATH", "")
    monkeypatch.setattr(settings, "BOT_RELOAD_COMMAND", "python -c pass")
    monkeypatch.setattr(settings, "BOT_RELOAD_ENABLED", True)
    monkeypatch.setattr(settings, "BOT_RELOAD_TIMEOUT_SEC", 5)

    async def _fail_command():
        return ReloadResult(ok=False, method="command", detail="exit 1")

    with patch("app.services.bot_update_service._run_reload_command", _fail_command):
        result = await execute_configured_reload(settings.DEVELOPER_ID)
    assert result.ok is False


@pytest.mark.asyncio
async def test_command_timeout_handled(monkeypatch):
    monkeypatch.setattr(settings, "BOT_RELOAD_SENTINEL_PATH", "")
    monkeypatch.setattr(settings, "BOT_RELOAD_COMMAND", "python -c pass")
    monkeypatch.setattr(settings, "BOT_RELOAD_ENABLED", True)
    monkeypatch.setattr(settings, "BOT_RELOAD_TIMEOUT_SEC", 1)

    async def _slow_communicate():
        await asyncio.sleep(2)
        return b"", b""

    mock_proc = MagicMock()
    mock_proc.communicate = _slow_communicate
    mock_proc.kill = MagicMock()
    mock_proc.returncode = 1

    with patch(
        "app.services.bot_update_service.asyncio.create_subprocess_exec",
        AsyncMock(return_value=mock_proc),
    ):
        result = await execute_configured_reload(settings.DEVELOPER_ID)

    assert result.ok is False
    assert result.detail == "timeout"


@pytest.mark.asyncio
async def test_command_uses_create_subprocess_exec_not_shell(monkeypatch):
    monkeypatch.setattr(settings, "BOT_RELOAD_SENTINEL_PATH", "")
    monkeypatch.setattr(settings, "BOT_RELOAD_COMMAND", "echo ok")
    monkeypatch.setattr(settings, "BOT_RELOAD_ENABLED", True)

    with patch(
        "app.services.bot_update_service.asyncio.create_subprocess_exec",
        AsyncMock(side_effect=OSError("boom")),
    ) as exec_mock:
        result = await execute_configured_reload(settings.DEVELOPER_ID)

    assert result.ok is False
    exec_mock.assert_awaited_once()
    assert exec_mock.await_args.kwargs.get("shell") is None


def test_secrets_not_in_formatted_panel():
    mark_process_started()
    snap = _sample_snapshot(
        config_presence={
            "bot_token_set": True,
            "api_hash_set": True,
        }
    )
    text = format_bot_update_panel("en", snap)
    assert settings.BOT_TOKEN not in text
    assert settings.API_HASH not in text
