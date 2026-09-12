from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.config.settings import settings
from app.utils.i18n import t
from app.utils.ui import CB

sys.modules.setdefault("pyromod", SimpleNamespace())
sys.modules.setdefault("pyromod.exceptions", SimpleNamespace(ListenerStopped=Exception))


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []
        self.message_handlers: list = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ARG002
        def deco(fn):
            self.callback_handlers.append(fn)
            return fn
        return deco

    def on_message(self, *args, **kwargs):  # noqa: ARG002
        def deco(fn):
            self.message_handlers.append(fn)
            return fn
        return deco


def _handler_by_name(handlers: list, name: str):
    for fn in handlers:
        if fn.__name__ == name:
            return fn
    raise AssertionError(f"handler not found: {name}")


def _group_query(data: str, *, user_id: int = 55555):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="User"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=-100555, type=SimpleNamespace(value="supergroup")),
            text="Now playing",
            edit_text=AsyncMock(),
            delete=AsyncMock(),
        ),
    )


def _client():
    return SimpleNamespace(
        get_chat_member=AsyncMock(
            return_value=SimpleNamespace(status=SimpleNamespace(value="administrator"))
        )
    )


def _no_credit_patches():
    return (
        patch(
            "app.utils.playback_auth.group_runtime_state_service.get_runtime_credit_state",
            AsyncMock(return_value=SimpleNamespace(has_runtime_credit=False, settings=None)),
        ),
        patch("app.utils.playback_auth.settings_repo.get_chat_settings", AsyncMock(return_value=None)),
    )


@pytest.mark.asyncio
async def test_playback_control_callback_requires_credit_before_pause():
    from app.handlers import callbacks

    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "pb_playback_controls")
    query = _group_query(CB["PB_PAUSE"])
    credit_patch, settings_patch = _no_credit_patches()

    with (
        credit_patch,
        settings_patch,
        patch("app.services.CallService.pause", AsyncMock(return_value=True)) as pause_mock,
    ):
        await handler(_client(), query)

    pause_mock.assert_not_awaited()
    query.answer.assert_awaited_once_with(t("fa", "playback_cmd.no_credit"), show_alert=True)


@pytest.mark.asyncio
async def test_search_play_callback_requires_authorization_before_ytdlp():
    from app.handlers import search

    bot = _RecorderBot()
    search.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "on_search_select")
    query = _group_query("search:play:dQw4w9WgXcQ")
    credit_patch, settings_patch = _no_credit_patches()

    with (
        credit_patch,
        settings_patch,
        patch("app.handlers.search.MediaService.get_stream_url", AsyncMock(return_value="https://93.184.216.34/a.mp3")) as stream_mock,
        patch("app.handlers.search.CallService.join_voice_chat", AsyncMock(return_value=True)) as join_mock,
    ):
        await handler(_client(), query)

    stream_mock.assert_not_awaited()
    join_mock.assert_not_awaited()
    query.answer.assert_awaited_once_with(t("fa", "playback_cmd.no_credit"), show_alert=True)


@pytest.mark.asyncio
async def test_tv_radio_play_callback_requires_authorization_before_join():
    from app.handlers import tv_radio

    bot = _RecorderBot()
    tv_radio.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "on_tv_select")
    query = _group_query("pb:tv:t1")
    credit_patch, settings_patch = _no_credit_patches()

    with (
        credit_patch,
        settings_patch,
        patch("app.handlers.tv_radio._load_json", return_value=[{"id": "t1", "name": "TV", "url": "https://93.184.216.34/tv.m3u8"}]),
        patch("app.handlers.tv_radio.CallService.join_voice_chat", AsyncMock(return_value=True)) as join_mock,
    ):
        await handler(_client(), query)

    join_mock.assert_not_awaited()
    query.answer.assert_awaited_once_with(t("fa", "playback_cmd.no_credit"), show_alert=True)


def _promotion_message(text: str, *, user_id: int = 44444):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="Actor"),
        chat=SimpleNamespace(id=-100777, type=SimpleNamespace(value="supergroup")),
        text=text,
        reply_to_message=None,
        reply=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_music_admin_cannot_set_player_owner():
    from app.handlers import promotion

    bot = _RecorderBot()
    promotion.register(bot, None)
    handler = _handler_by_name(bot.message_handlers, "set_creator")
    message = _promotion_message("setcreator 99999")

    with (
        patch("app.utils.player_permissions.can_manage_player_owner", AsyncMock(return_value=False)),
        patch("app.handlers.promotion.admin_repo.promote_player_owner", AsyncMock()) as promote_mock,
    ):
        await handler(SimpleNamespace(), message)

    promote_mock.assert_not_awaited()
    message.reply.assert_awaited_once_with(t("fa", "common.errors.no_access"))


@pytest.mark.asyncio
async def test_player_owner_cannot_set_player_owner():
    from app.handlers import promotion

    bot = _RecorderBot()
    promotion.register(bot, None)
    handler = _handler_by_name(bot.message_handlers, "set_creator")
    message = _promotion_message("setcreator 99999")

    with (
        patch("app.utils.player_permissions.can_manage_player_owner", AsyncMock(return_value=False)),
        patch("app.handlers.promotion.admin_repo.promote_player_owner", AsyncMock()) as promote_mock,
    ):
        await handler(SimpleNamespace(), message)

    promote_mock.assert_not_awaited()
    message.reply.assert_awaited_once_with(t("fa", "common.errors.no_access"))


@pytest.mark.asyncio
async def test_top_authority_can_set_player_owner():
    from app.handlers import promotion

    bot = _RecorderBot()
    promotion.register(bot, None)
    handler = _handler_by_name(bot.message_handlers, "set_creator")
    message = _promotion_message("setcreator 99999")

    with (
        patch("app.utils.player_permissions.can_manage_player_owner", AsyncMock(return_value=True)),
        patch("app.handlers.promotion.admin_repo.promote_player_owner", AsyncMock()) as promote_mock,
    ):
        await handler(SimpleNamespace(), message)

    promote_mock.assert_awaited_once()


def test_parse_bounded_int_rejects_negative_zero_and_overflow():
    from app.utils.helpers import MAX_CREDIT_DAYS, parse_bounded_int

    assert parse_bounded_int("-1", min_value=1, max_value=MAX_CREDIT_DAYS) is None
    assert parse_bounded_int("0", min_value=1, max_value=MAX_CREDIT_DAYS) is None
    assert parse_bounded_int(str(MAX_CREDIT_DAYS + 1), min_value=1, max_value=MAX_CREDIT_DAYS) is None
    assert parse_bounded_int("30", min_value=1, max_value=MAX_CREDIT_DAYS) == 30


@pytest.mark.asyncio
async def test_credit_service_rejects_invalid_days_before_lock():
    from app.services.credit_service import CreditService

    with patch("app.services.credit_service.acquire_lock", AsyncMock()) as lock_mock:
        with pytest.raises(ValueError):
            await CreditService.charge(-1001, "group", -5, operated_by=settings.DEVELOPER_ID)

    lock_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_credit_wallet_rejects_negative_rate_before_lock():
    from app.services.credit_service import CreditService

    with (
        patch("app.repositories.settings_repo.get_bot_setting", AsyncMock(return_value="-10")),
        patch("app.services.credit_service.acquire_lock", AsyncMock()) as lock_mock,
    ):
        with pytest.raises(ValueError):
            await CreditService.charge_with_wallet(
                -1001,
                "group",
                5,
                sudo_user_id=123,
                rate_key="music_rate",
            )

    lock_mock.assert_not_awaited()
