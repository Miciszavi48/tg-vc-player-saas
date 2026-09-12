"""Tests for Id command output mode, rich user info, and call-stats settings."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

from app.utils.i18n import AUTO_LANG, t
from app.utils.ui import CB


class _RecorderBot:
    def __init__(self) -> None:
        self.message_handlers: list = []
        self.callback_handlers: list = []

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.message_handlers.append(fn)
            return fn

        return _decorator

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator


def _registered_group_panel():
    from app.handlers import group_panel

    bot = _RecorderBot()
    group_panel.register(bot, MagicMock())
    return bot


def _message(
    text: str,
    *,
    chat_id: int = -7001,
    user_id: int = 42,
    reply_user_id: int | None = None,
    chat_type: str = "supergroup",
    first_name: str = "Tester",
    username: str | None = "tester",
):
    reply_to = None
    if reply_user_id is not None:
        reply_to = SimpleNamespace(
            from_user=SimpleNamespace(
                id=reply_user_id,
                first_name="Replied",
                username="replied",
            )
        )
    return SimpleNamespace(
        text=text,
        caption=None,
        chat=SimpleNamespace(id=chat_id, type=SimpleNamespace(value=chat_type)),
        from_user=SimpleNamespace(id=user_id, first_name=first_name, username=username),
        reply_to_message=reply_to,
        reply=AsyncMock(),
        reply_photo=AsyncMock(),
    )


def _query(data: str, *, chat_id: int = -7001, user_id: int = 42):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=chat_id, type=SimpleNamespace(value="supergroup")),
            edit_text=AsyncMock(),
        ),
        answer=AsyncMock(),
    )


def _handler(handlers: list, name: str):
    return next(fn for fn in handlers if fn.__name__ == name)


@pytest.mark.asyncio
async def test_show_id_photo_sets_output_mode_not_cover():
    bot = _registered_group_panel()
    photo = _handler(bot.message_handlers, "show_id_photo_command")
    message = _message("Show Id Status Photo")

    with (
        patch("app.utils.decorators.is_developer", return_value=True),
        patch("app.handlers.group_panel._guard_group_chat_settings", AsyncMock(return_value=True)),
        patch(
            "app.handlers.group_panel.id_command_settings_repo.set_output_mode",
            AsyncMock(),
        ) as set_mode,
        patch(
            "app.handlers.group_panel.settings_repo.update_setting",
            AsyncMock(),
        ) as update_setting,
    ):
        await photo(AsyncMock(), message)

    set_mode.assert_awaited_once_with(message.chat.id, "photo", updated_by=message.from_user.id)
    update_setting.assert_not_awaited()
    message.reply.assert_awaited_once_with(t("fa", "public_cmd.show_id_mode_photo_set"))


@pytest.mark.asyncio
async def test_show_id_simple_sets_output_mode_not_track_id():
    bot = _registered_group_panel()
    simple = _handler(bot.message_handlers, "show_id_simple_command")
    message = _message("Show Id Status Simple")

    with (
        patch("app.utils.decorators.is_developer", return_value=True),
        patch("app.handlers.group_panel._guard_group_chat_settings", AsyncMock(return_value=True)),
        patch(
            "app.handlers.group_panel.id_command_settings_repo.set_output_mode",
            AsyncMock(),
        ) as set_mode,
        patch(
            "app.handlers.group_panel.settings_repo.update_setting",
            AsyncMock(),
        ) as update_setting,
    ):
        await simple(AsyncMock(), message)

    set_mode.assert_awaited_once_with(message.chat.id, "simple", updated_by=message.from_user.id)
    update_setting.assert_not_awaited()
    message.reply.assert_awaited_once_with(t("fa", "public_cmd.show_id_mode_simple_set"))


@pytest.mark.asyncio
async def test_user_id_simple_mode_replies_text_only():
    bot = _registered_group_panel()
    handler = _handler(bot.message_handlers, "user_id_command")
    message = _message("Id", user_id=100)
    client = AsyncMock()

    with (
        patch(
            "app.handlers.group_panel.id_command_settings_repo.get_output_mode",
            AsyncMock(return_value="simple"),
        ),
        patch(
            "app.handlers.group_panel.call_stats_settings_repo.is_effective_id_call_stats",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.group_panel.user_info_formatter_service.reply_user_info",
            AsyncMock(),
        ) as reply_info,
    ):
        await handler(client, message)

    reply_info.assert_awaited_once_with(
        client,
        message,
        output_mode="simple",
        include_call_stats=True,
        lang=AUTO_LANG,
    )


@pytest.mark.asyncio
async def test_user_id_photo_mode_uses_formatter():
    bot = _registered_group_panel()
    handler = _handler(bot.message_handlers, "user_id_command")
    message = _message("آیدی", user_id=100, reply_user_id=200)
    client = AsyncMock()

    with (
        patch(
            "app.handlers.group_panel.id_command_settings_repo.get_output_mode",
            AsyncMock(return_value="photo"),
        ),
        patch(
            "app.handlers.group_panel.call_stats_settings_repo.is_effective_id_call_stats",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.handlers.group_panel.user_info_formatter_service.reply_user_info",
            AsyncMock(),
        ) as reply_info,
    ):
        await handler(client, message)

    reply_info.assert_awaited_once_with(
        client,
        message,
        output_mode="photo",
        include_call_stats=False,
        lang=AUTO_LANG,
    )


@pytest.mark.asyncio
async def test_reply_user_info_photo_mode_sends_photo_with_caption():
    from app.services import user_info_formatter_service

    message = _message("Id", user_id=100)
    client = AsyncMock()
    photo = SimpleNamespace(file_id="photo123")

    async def _photos(_uid, limit=1):  # noqa: ANN001
        yield photo

    client.get_chat_photos = _photos

    with patch.object(
        user_info_formatter_service,
        "build_user_info_text",
        AsyncMock(return_value="caption body"),
    ):
        await user_info_formatter_service.reply_user_info(
            client,
            message,
            output_mode="photo",
            include_call_stats=False,
            lang="fa",
        )

    message.reply_photo.assert_awaited_once_with("photo123", caption="caption body")
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_reply_user_info_photo_mode_falls_back_to_text():
    from app.services import user_info_formatter_service

    message = _message("Id", user_id=100)
    client = AsyncMock()

    async def _empty_photos(_uid, limit=1):  # noqa: ANN001
        if False:  # pragma: no cover - async generator
            yield None

    client.get_chat_photos = _empty_photos

    with patch.object(
        user_info_formatter_service,
        "build_user_info_text",
        AsyncMock(return_value="text body"),
    ):
        await user_info_formatter_service.reply_user_info(
            client,
            message,
            output_mode="photo",
            include_call_stats=False,
            lang="fa",
        )

    message.reply.assert_awaited_once_with("text body")
    message.reply_photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_user_info_includes_stats_when_enabled():
    from app.services import user_info_formatter_service

    user = SimpleNamespace(id=100, first_name="Ali", username="ali", last_name=None)
    client = AsyncMock()
    client.get_users = AsyncMock(return_value=SimpleNamespace(dc_id=2))

    with (
        patch.object(
            user_info_formatter_service,
            "resolve_role_label",
            AsyncMock(return_value="کاربر عادی"),
        ),
        patch.object(
            user_info_formatter_service,
            "build_call_stats_block",
            AsyncMock(return_value="─┅━✦━┅─\n◂ زمان حضور در ویسکال : 00:10:00"),
        ),
    ):
        text = await user_info_formatter_service.build_user_info_text(
            client=client,
            user=user,
            chat_id=-7001,
            lang="fa",
            include_call_stats=True,
        )

    assert "• نام : Ali" in text
    assert "• یوزرنیم : @ali" in text
    assert "• کد دیتاسنتر : 2" in text
    assert "• آیدی عددی : 100" in text
    assert "زمان حضور در ویسکال" in text


@pytest.mark.asyncio
async def test_build_user_info_omits_stats_when_disabled():
    from app.services import user_info_formatter_service

    user = SimpleNamespace(id=100, first_name="Ali", username=None, last_name=None)
    client = AsyncMock()
    client.get_users = AsyncMock(side_effect=RuntimeError("no dc"))

    with (
        patch.object(
            user_info_formatter_service,
            "resolve_role_label",
            AsyncMock(return_value="کاربر عادی"),
        ),
        patch.object(
            user_info_formatter_service,
            "build_call_stats_block",
            AsyncMock(),
        ) as stats_block,
    ):
        text = await user_info_formatter_service.build_user_info_text(
            client=client,
            user=user,
            chat_id=-7001,
            lang="fa",
            include_call_stats=False,
        )

    stats_block.assert_not_awaited()
    assert "زمان حضور در ویسکال" not in text
    assert "نامشخص" in text
    assert "• یوزرنیم : -" in text
    assert "+989" not in text
    assert "C:\\" not in text


@pytest.mark.asyncio
async def test_id_call_stats_toggle_requires_permission():
    bot = _registered_group_panel()
    handler = _handler(bot.callback_handlers, "grp_toggle_id_call_stats")
    query = _query(CB["GRP_ID_CALL_STATS"])

    with (
        patch("app.utils.decorators.is_developer", return_value=False),
        patch("app.handlers.group_panel._guard_group_chat_settings", AsyncMock(return_value=False)),
        patch(
            "app.handlers.group_panel.id_command_settings_repo.set_show_call_stats",
            AsyncMock(),
        ) as set_stats,
    ):
        await handler(AsyncMock(), query)

    set_stats.assert_not_awaited()
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_id_call_stats_toggle_flips_setting():
    bot = _registered_group_panel()
    handler = _handler(bot.callback_handlers, "grp_toggle_id_call_stats")
    query = _query(CB["GRP_ID_CALL_STATS"])

    with (
        patch("app.utils.decorators.is_developer", return_value=True),
        patch("app.handlers.group_panel._guard_group_chat_settings", AsyncMock(return_value=True)),
        patch(
            "app.handlers.group_panel.call_stats_settings_repo.get_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.group_panel.id_command_settings_repo.get_show_call_stats",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.handlers.group_panel.id_command_settings_repo.set_show_call_stats",
            AsyncMock(),
        ) as set_stats,
        patch(
            "app.handlers.group_panel._get_settings_dict",
            AsyncMock(return_value={"id_call_stats": False}),
        ),
        patch(
            "app.handlers.group_panel._build_group_settings_summary",
            AsyncMock(return_value="summary"),
        ),
    ):
        await handler(AsyncMock(), query)

    set_stats.assert_awaited_once_with(query.message.chat.id, True, updated_by=query.from_user.id)
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_id_call_stats_toggle_requires_main_call_stats_enabled():
    bot = _registered_group_panel()
    handler = _handler(bot.callback_handlers, "grp_toggle_id_call_stats")
    query = _query(CB["GRP_ID_CALL_STATS"])

    with (
        patch("app.utils.decorators.is_developer", return_value=True),
        patch("app.handlers.group_panel._guard_group_chat_settings", AsyncMock(return_value=True)),
        patch(
            "app.handlers.group_panel.call_stats_settings_repo.get_enabled",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.handlers.group_panel.id_command_settings_repo.set_show_call_stats",
            AsyncMock(),
        ) as set_stats,
    ):
        await handler(AsyncMock(), query)

    set_stats.assert_not_awaited()
    query.answer.assert_awaited_once_with(
        t("fa", "panels.group.settings.id_call_stats_off_reason"),
        show_alert=True,
    )
    query.message.edit_text.assert_not_awaited()


def test_truncate_caption_drops_stats_block_first():
    from app.services.user_info_formatter_service import truncate_caption

    base = "A" * 1010
    separator = t("fa", "public_cmd.user_info_stats_separator")
    stats = "◂ زمان حضور در ویسکال : 00:00:01"
    long_text = f"{base}\n{separator}\n{stats}"
    trimmed = truncate_caption(long_text, max_len=1024)
    assert len(trimmed) <= 1024
    assert separator not in trimmed
