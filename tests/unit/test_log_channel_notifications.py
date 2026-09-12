"""Tests for log channel resolution and notification routing."""
from __future__ import annotations

import os
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

from app.config.settings import settings
from app.services.bot_settings_service import parse_log_channel_id, resolve_log_channel_id
from app.services.notification_service import NotificationService
from app.utils.ask_result import AskResult
from app.utils.i18n import label


class TestParseLogChannelId:
    def test_none_and_zero_are_unset(self):
        assert parse_log_channel_id(None) is None
        assert parse_log_channel_id("") is None
        assert parse_log_channel_id("0") is None
        assert parse_log_channel_id(0) is None

    def test_valid_negative_channel(self):
        assert parse_log_channel_id("-1001234567890") == -1001234567890


@pytest.mark.asyncio
class TestResolveLogChannelId:
    async def test_prefers_db_over_env(self):
        with (
            patch(
                "app.services.bot_settings_service.settings_repo.get_bot_setting",
                AsyncMock(return_value="-100111"),
            ),
            patch.object(settings, "LOG_CHANNEL_ID", -100222),
        ):
            assert await resolve_log_channel_id() == -100111

    async def test_falls_back_to_env_when_db_missing(self):
        with (
            patch(
                "app.services.bot_settings_service.settings_repo.get_bot_setting",
                AsyncMock(return_value=None),
            ),
            patch.object(settings, "LOG_CHANNEL_ID", -100333),
        ):
            assert await resolve_log_channel_id() == -100333

    async def test_db_zero_falls_back_to_env(self):
        with (
            patch(
                "app.services.bot_settings_service.settings_repo.get_bot_setting",
                AsyncMock(return_value="0"),
            ),
            patch.object(settings, "LOG_CHANNEL_ID", -100444),
        ):
            assert await resolve_log_channel_id() == -100444

    async def test_returns_none_when_both_unset(self):
        with (
            patch(
                "app.services.bot_settings_service.settings_repo.get_bot_setting",
                AsyncMock(return_value=None),
            ),
            patch.object(settings, "LOG_CHANNEL_ID", 0),
        ):
            assert await resolve_log_channel_id() is None

    async def test_db_failure_falls_back_to_env(self):
        with (
            patch(
                "app.services.bot_settings_service.settings_repo.get_bot_setting",
                AsyncMock(side_effect=RuntimeError("db down")),
            ),
            patch.object(settings, "LOG_CHANNEL_ID", -100555),
        ):
            assert await resolve_log_channel_id() == -100555


@pytest.mark.asyncio
class TestSendToLogChannel:
    async def test_uses_resolved_channel(self):
        bot = AsyncMock()
        with (
            patch(
                "app.services.notification_service.resolve_log_channel_id",
                AsyncMock(return_value=-100777),
            ),
            patch(
                "app.utils.safe_sender.safe_send_message",
                AsyncMock(return_value=True),
            ) as send_mock,
        ):
            await NotificationService.send_to_log_channel(bot, "hello")
        send_mock.assert_awaited_once_with(bot, -100777, "hello", mark_unreachable=False)

    async def test_skips_when_no_channel(self):
        bot = AsyncMock()
        with (
            patch(
                "app.services.notification_service.resolve_log_channel_id",
                AsyncMock(return_value=None),
            ),
            patch(
                "app.utils.safe_sender.safe_send_message",
                AsyncMock(return_value=True),
            ) as send_mock,
        ):
            await NotificationService.send_to_log_channel(bot, "hello")
        send_mock.assert_not_awaited()

    async def test_send_failure_does_not_raise(self):
        bot = AsyncMock()
        with (
            patch(
                "app.services.notification_service.resolve_log_channel_id",
                AsyncMock(return_value=-100888),
            ),
            patch(
                "app.utils.safe_sender.safe_send_message",
                AsyncMock(side_effect=RuntimeError("telegram down")),
            ),
        ):
            await NotificationService.send_to_log_channel(bot, "hello")


@pytest.mark.asyncio
class TestEventNotifications:
    async def test_notify_install_uses_log_channel(self):
        bot = AsyncMock()
        with patch.object(
            NotificationService,
            "send_to_log_channel",
            AsyncMock(),
        ) as send_mock:
            await NotificationService.notify_install(
                bot,
                -1001,
                "group",
                "Test Group",
                sudo_info="42",
                installer_role="sudo",
                policy_mode="paid",
            )
        send_mock.assert_awaited_once()
        text = send_mock.await_args.args[1]
        assert "Test Group" in text
        assert label("fa", "installer_role", "sudo") in text
        assert label("fa", "install_policy_mode", "paid") in text

    async def test_notify_sudo_added(self):
        bot = AsyncMock()
        with patch.object(
            NotificationService,
            "send_to_log_channel",
            AsyncMock(),
        ) as send_mock:
            await NotificationService.notify_sudo_added(
                bot, 9001, settings.DEVELOPER_ID, username="sudo_user"
            )
        text = send_mock.await_args.args[1]
        assert "9001" in text
        assert "@sudo_user" in text

    async def test_notify_sudo_removed(self):
        bot = AsyncMock()
        with patch.object(
            NotificationService,
            "send_to_log_channel",
            AsyncMock(),
        ) as send_mock:
            await NotificationService.notify_sudo_removed(bot, 9002, settings.DEVELOPER_ID)
        text = send_mock.await_args.args[1]
        assert "9002" in text

    async def test_notify_bot_start(self):
        bot = AsyncMock()
        with patch.object(
            NotificationService,
            "send_to_log_channel",
            AsyncMock(),
        ) as send_mock:
            await NotificationService.notify_bot_start(
                bot, 42, username="dev_user", role="developer"
            )
        text = send_mock.await_args.args[1]
        assert label("fa", "installer_role", "developer") in text
        assert "42" in text


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


@pytest.mark.asyncio
class TestDevSetLogChannelSave:
    async def test_dev_set_log_channel_persists_setting(self):
        from app.handlers import dev_panel
        from app.utils.ui import CB

        bot = _RecorderBot()
        dev_panel.register(bot, None)
        handler = next(
            fn for fn in bot.callback_handlers if fn.__name__ == "dev_set_log_channel"
        )

        query = AsyncMock()
        query.from_user.id = settings.DEVELOPER_ID
        query.message.chat.id = settings.DEVELOPER_ID
        query.data = CB["DEV_SET_LOG_CHANNEL"]
        query.answer = AsyncMock()

        ask_message = SimpleNamespace(text="-100999888777")

        with (
            patch(
                "app.handlers.dev_panel._ask",
                AsyncMock(return_value=AskResult(message=ask_message)),
            ),
            patch(
                "app.handlers.dev_panel.settings_repo.set_bot_setting",
                AsyncMock(),
            ) as set_mock,
            patch("app.handlers.dev_panel._send_done", AsyncMock()),
        ):
            await handler(bot, query)

        set_mock.assert_awaited_once()
        assert set_mock.await_args.args[0] == "log_channel_id"
        assert set_mock.await_args.args[1] == "-100999888777"


@pytest.mark.asyncio
class TestDevPanelSudoNotifications:
    async def test_sudo_add_triggers_notification(self):
        from app.handlers import dev_panel

        bot = AsyncMock()
        recorder = type("B", (), {"callback_handlers": []})()
        recorder.on_callback_query = lambda *a, **k: (lambda fn: recorder.callback_handlers.append(fn) or fn)
        for attr in ("on_message", "on_chat_member_updated"):
            setattr(recorder, attr, lambda *a, **k: (lambda fn: fn))

        dev_panel.register(recorder, None)
        handler = next(
            fn for fn in recorder.callback_handlers if fn.__name__ == "dev_sudo_manage"
        )

        query = AsyncMock()
        query.from_user.id = settings.DEVELOPER_ID
        query.message.chat.id = settings.DEVELOPER_ID
        query.message.edit_text = AsyncMock()
        query.answer = AsyncMock()

        ask_message = SimpleNamespace(text="12345")

        sudo_row = type("S", (), {"username": "new_sudo"})()

        with (
            patch("app.handlers.dev_panel.user_repo.get_all_sudos", AsyncMock(return_value=[])),
            patch(
                "app.handlers.dev_panel._ask",
                AsyncMock(return_value=AskResult(message=ask_message)),
            ),
            patch("app.handlers.dev_panel.user_repo.add_sudo", AsyncMock()),
            patch("app.handlers.dev_panel.user_repo.get_sudo", AsyncMock(return_value=sudo_row)),
            patch("app.handlers.dev_panel.invalidate_sudolist", AsyncMock()),
            patch("app.handlers.dev_panel._send_done", AsyncMock()),
            patch(
                "app.handlers.dev_panel.NotificationService.notify_sudo_added",
                AsyncMock(),
            ) as notify_mock,
        ):
            await handler(bot, query)

        notify_mock.assert_awaited_once()

    async def test_sudo_remove_confirm_triggers_notification(self):
        from app.handlers import dev_panel
        from app.utils.ui import CB

        bot = AsyncMock()
        recorder = type("B", (), {"callback_handlers": []})()
        recorder.on_callback_query = lambda *a, **k: (lambda fn: recorder.callback_handlers.append(fn) or fn)
        for attr in ("on_message", "on_chat_member_updated"):
            setattr(recorder, attr, lambda *a, **k: (lambda fn: fn))

        dev_panel.register(recorder, None)
        handler = next(
            fn for fn in recorder.callback_handlers if fn.__name__ == "dev_sudo_remove_confirm"
        )

        import time

        issued_at = int(time.time())
        query = AsyncMock()
        query.from_user.id = settings.DEVELOPER_ID
        query.message.chat.id = settings.DEVELOPER_ID
        query.data = f"{CB['DEV_SUDO_REMOVE_EXEC_PREFIX']}9003:{settings.DEVELOPER_ID}:{issued_at}"
        query.answer = AsyncMock()

        sudo_row = type("S", (), {"user_id": 9003})()

        with (
            patch("app.handlers.dev_panel.user_repo.get_sudo", AsyncMock(return_value=sudo_row)),
            patch("app.handlers.dev_panel.user_repo.remove_sudo", AsyncMock()),
            patch("app.handlers.dev_panel.invalidate_sudolist", AsyncMock()),
            patch("app.handlers.dev_panel._send_done", AsyncMock()),
            patch(
                "app.handlers.dev_panel.NotificationService.notify_sudo_removed",
                AsyncMock(),
            ) as notify_mock,
        ):
            await handler(bot, query)

        notify_mock.assert_awaited_once_with(bot, 9003, settings.DEVELOPER_ID)


def _gc_memory_patches(*, percent: float = 75.0):
    """Common patches for gc_and_memory_check scheduler job."""
    mem_info = SimpleNamespace(rss=100 * 1024 * 1024)
    sys_mem = SimpleNamespace(percent=percent)
    return (
        patch("psutil.Process", return_value=SimpleNamespace(memory_info=lambda: mem_info)),
        patch("psutil.virtual_memory", return_value=sys_mem),
        patch("psutil.process_iter", return_value=[]),
        patch("app.scheduler.gc.collect"),
        patch("app.services.watchdog.run_full_watchdog", AsyncMock()),
    )


class TestSchedulerLogChannelStatic:
    def test_scheduler_no_direct_log_channel_id_usage(self):
        src = Path("app/scheduler.py").read_text(encoding="utf-8")
        assert "settings.LOG_CHANNEL_ID" not in src


@pytest.mark.asyncio
class TestSchedulerMemoryAlert:
    async def test_notify_memory_high_uses_send_to_log_channel(self):
        bot = AsyncMock()
        with patch.object(
            NotificationService,
            "send_to_log_channel",
            AsyncMock(),
        ) as send_mock:
            await NotificationService.notify_memory_high(bot, usage="75")
        text = send_mock.await_args.args[1]
        assert "75" in text

    async def test_gc_and_memory_check_calls_notify_memory_high(self):
        import app.scheduler as sched

        bot = AsyncMock()
        notify_mock = AsyncMock()
        with ExitStack() as stack:
            for ctx in _gc_memory_patches(percent=75.0):
                stack.enter_context(ctx)
            stack.enter_context(patch.object(sched, "_bot", bot))
            stack.enter_context(patch.object(sched, "_call_py", None))
            stack.enter_context(
                patch.object(NotificationService, "notify_memory_high", notify_mock)
            )
            await sched.gc_and_memory_check()

        notify_mock.assert_awaited_once_with(bot, usage="75")

    async def test_gc_and_memory_check_uses_db_log_channel(self):
        import app.scheduler as sched

        bot = AsyncMock()
        send_mock = AsyncMock(return_value=True)
        with ExitStack() as stack:
            for ctx in _gc_memory_patches(percent=80.0):
                stack.enter_context(ctx)
            stack.enter_context(patch.object(sched, "_bot", bot))
            stack.enter_context(patch.object(sched, "_call_py", None))
            stack.enter_context(
                patch(
                    "app.services.notification_service.resolve_log_channel_id",
                    AsyncMock(return_value=-100111),
                )
            )
            stack.enter_context(
                patch("app.utils.safe_sender.safe_send_message", send_mock)
            )
            await sched.gc_and_memory_check()

        send_mock.assert_awaited_once()
        assert send_mock.await_args.args[1] == -100111
        assert "80" in send_mock.await_args.args[2]

    async def test_gc_and_memory_check_falls_back_to_env_log_channel(self):
        import app.scheduler as sched

        bot = AsyncMock()
        send_mock = AsyncMock(return_value=True)
        with ExitStack() as stack:
            for ctx in _gc_memory_patches(percent=72.0):
                stack.enter_context(ctx)
            stack.enter_context(patch.object(sched, "_bot", bot))
            stack.enter_context(patch.object(sched, "_call_py", None))
            stack.enter_context(
                patch(
                    "app.services.bot_settings_service.settings_repo.get_bot_setting",
                    AsyncMock(return_value=None),
                )
            )
            stack.enter_context(patch.object(settings, "LOG_CHANNEL_ID", -100333))
            stack.enter_context(
                patch("app.utils.safe_sender.safe_send_message", send_mock)
            )
            await sched.gc_and_memory_check()

        send_mock.assert_awaited_once()
        assert send_mock.await_args.args[1] == -100333

    async def test_gc_and_memory_check_skips_when_no_channel(self):
        import app.scheduler as sched

        bot = AsyncMock()
        send_mock = AsyncMock(return_value=True)
        with ExitStack() as stack:
            for ctx in _gc_memory_patches(percent=85.0):
                stack.enter_context(ctx)
            stack.enter_context(patch.object(sched, "_bot", bot))
            stack.enter_context(patch.object(sched, "_call_py", None))
            stack.enter_context(
                patch(
                    "app.services.notification_service.resolve_log_channel_id",
                    AsyncMock(return_value=None),
                )
            )
            stack.enter_context(
                patch("app.utils.safe_sender.safe_send_message", send_mock)
            )
            await sched.gc_and_memory_check()

        send_mock.assert_not_awaited()

    async def test_gc_and_memory_check_send_failure_no_crash(self):
        import app.scheduler as sched

        bot = AsyncMock()
        with ExitStack() as stack:
            for ctx in _gc_memory_patches(percent=90.0):
                stack.enter_context(ctx)
            stack.enter_context(patch.object(sched, "_bot", bot))
            stack.enter_context(patch.object(sched, "_call_py", None))
            stack.enter_context(
                patch.object(
                    NotificationService,
                    "notify_memory_high",
                    AsyncMock(side_effect=RuntimeError("telegram down")),
                )
            )
            await sched.gc_and_memory_check()

    async def test_gc_and_memory_check_below_threshold_skips_notify(self):
        import app.scheduler as sched

        bot = AsyncMock()
        notify_mock = AsyncMock()
        with ExitStack() as stack:
            for ctx in _gc_memory_patches(percent=50.0):
                stack.enter_context(ctx)
            stack.enter_context(patch.object(sched, "_bot", bot))
            stack.enter_context(patch.object(sched, "_call_py", None))
            stack.enter_context(
                patch.object(NotificationService, "notify_memory_high", notify_mock)
            )
            await sched.gc_and_memory_check()

        notify_mock.assert_not_awaited()
