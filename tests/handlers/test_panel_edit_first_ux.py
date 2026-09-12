"""Edit-first panel navigation UX regression tests."""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pyrogram.types import InlineKeyboardMarkup

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


def _message(*, text: str = "panel", caption: str | None = None):
    return SimpleNamespace(
        id=100,
        text=text if caption is None else None,
        caption=caption,
        chat=SimpleNamespace(id=42, type=SimpleNamespace(value="private")),
        edit_text=AsyncMock(),
        edit_caption=AsyncMock(),
        edit_reply_markup=AsyncMock(),
        delete=AsyncMock(),
    )


def _query(message=None, user_id: int = 7):
    message = message or _message()
    return SimpleNamespace(
        id="q1",
        from_user=SimpleNamespace(id=user_id),
        message=message,
        answer=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_safe_edit_message_treats_message_not_modified_as_success():
    from app.utils.telegram_message import safe_edit_message

    class _MessageNotModified(Exception):
        pass

    msg = _message()
    msg.edit_text = AsyncMock(side_effect=_MessageNotModified("MESSAGE_NOT_MODIFIED"))
    assert await safe_edit_message(msg, "same text") is True


@pytest.mark.asyncio
async def test_panel_callback_edit_does_not_send_when_edit_succeeds():
    from app.services.panel_message_service import panel_callback_edit

    client = SimpleNamespace(send_message=AsyncMock())
    query = _query()
    with patch(
        "app.services.panel_message_service.remember_panel_from_query",
        new_callable=AsyncMock,
    ):
        ok = await panel_callback_edit(client, query, "updated", None)
    assert ok is True
    client.send_message.assert_not_called()
    query.message.edit_text.assert_awaited()


@pytest.mark.asyncio
async def test_deliver_panel_outcome_edits_stored_panel_instead_of_send():
    from app.services.panel_message_service import deliver_panel_outcome

    client = SimpleNamespace(
        edit_message_text=AsyncMock(),
        send_message=AsyncMock(),
    )
    with patch(
        "app.services.panel_message_service.get_panel_message_id",
        new_callable=AsyncMock,
        return_value=55,
    ):
        await deliver_panel_outcome(
            client, 42, 7, "done", None,
        )
    client.edit_message_text.assert_awaited_once()
    client.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_panel_outcome_single_send_fallback_on_edit_failure():
    from app.services.panel_message_service import deliver_panel_outcome

    msg = _message()
    msg.edit_text = AsyncMock(side_effect=RuntimeError("not editable"))
    msg.edit_caption = AsyncMock(side_effect=RuntimeError("not editable"))
    msg.edit_reply_markup = AsyncMock(side_effect=RuntimeError("not editable"))
    client = SimpleNamespace(
        edit_message_text=AsyncMock(side_effect=RuntimeError("gone")),
        edit_message_caption=AsyncMock(side_effect=RuntimeError("gone")),
        send_message=AsyncMock(return_value=SimpleNamespace(id=200, chat=msg.chat)),
    )
    with patch(
        "app.services.panel_message_service.get_panel_message_id",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "app.services.panel_message_service.remember_panel_message",
        new_callable=AsyncMock,
    ):
        sent = await deliver_panel_outcome(
            client, 42, 7, "fallback", None, query_message=msg,
        )
    assert client.send_message.await_count == 1
    assert sent is not None or msg is not None


@pytest.mark.asyncio
async def test_send_done_uses_deliver_not_raw_send():
    from app.handlers import dev_panel

    client = SimpleNamespace(send_message=AsyncMock())
    with patch.object(dev_panel, "deliver_ask_outcome", new_callable=AsyncMock) as deliver:
        await dev_panel._send_done(client, 42, "ok", dev_panel.TOKEN_DEV_RATES, user_id=7)
    deliver.assert_awaited_once()
    client.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_prompt_for_panel_input_edits_panel_then_listens_without_sending():
    from app.utils import ask_result

    response = SimpleNamespace(text="42")

    class _Client:
        def __init__(self) -> None:
            self.listen_calls: list[dict] = []
            self.send_message = AsyncMock()

        async def listen(self, **kwargs):
            self.listen_calls.append(kwargs)
            return response

    client = _Client()
    kb = InlineKeyboardMarkup([])
    with patch.object(
        ask_result,
        "deliver_ask_outcome",
        new_callable=AsyncMock,
    ) as deliver:
        result = await ask_result.prompt_for_panel_input(
            client,
            42,
            7,
            "type value",
            kb,
            timeout=30,
        )

    assert result is response
    deliver.assert_awaited_once_with(client, 42, 7, "type value", kb)
    assert len(client.listen_calls) == 1
    listen_call = client.listen_calls[0]
    assert listen_call["chat_id"] == 42
    assert listen_call["timeout"] == 30
    assert listen_call["user_id"] == 7
    assert await listen_call["filters"](
        client,
        SimpleNamespace(text="/help", caption=None),
    ) is False
    assert await listen_call["filters"](
        client,
        SimpleNamespace(text="42", caption=None),
    ) is True
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_safe_caption_edit_uses_caption_compatible_kwargs_only():
    from app.utils.telegram_message import safe_edit_message

    msg = _message(caption="media panel")
    assert await safe_edit_message(msg, "updated", disable_web_page_preview=True)

    msg.edit_caption.assert_awaited_once()
    assert "disable_web_page_preview" not in msg.edit_caption.await_args.kwargs
    msg.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_markup_only_edit_is_not_reported_as_panel_redraw():
    from app.utils.telegram_message import safe_edit_message

    msg = _message()
    msg.edit_text = AsyncMock(side_effect=RuntimeError("text failed"))
    msg.edit_caption = AsyncMock(side_effect=RuntimeError("caption failed"))

    assert await safe_edit_message(msg, "new text", reply_markup="kb") is False
    msg.edit_reply_markup.assert_not_awaited()


@pytest.mark.asyncio
async def test_panel_callback_edit_survives_redis_metadata_outage():
    from app.services.panel_message_service import panel_callback_edit

    query = _query()
    client = SimpleNamespace(send_message=AsyncMock())
    with patch(
        "app.services.panel_message_service.get_redis",
        new_callable=AsyncMock,
        side_effect=RuntimeError("redis down"),
    ):
        assert await panel_callback_edit(client, query, "updated") is True

    query.message.edit_text.assert_awaited()
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_foreign_chat_panel_anchor_is_ignored():
    from app.services.panel_message_service import get_panel_message_id

    redis = SimpleNamespace(
        get=AsyncMock(
            return_value='{"chat_id": 999, "message_id": 55}'
        )
    )
    with patch(
        "app.services.panel_message_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        assert await get_panel_message_id(42, 7) is None


@pytest.mark.asyncio
async def test_photo_preview_replaces_and_remembers_single_active_panel():
    from app.services.panel_message_service import replace_panel_with_photo

    old = _message()
    sent = _message(caption="preview")
    sent.id = 200
    client = SimpleNamespace(send_photo=AsyncMock(return_value=sent))
    query = _query(old)

    with patch(
        "app.services.panel_message_service.remember_panel_message",
        new_callable=AsyncMock,
    ) as remember:
        result = await replace_panel_with_photo(
            client,
            query,
            "photo-id",
            caption="preview",
            reply_markup="kb",
        )

    assert result is sent
    assert query.message is sent
    client.send_photo.assert_awaited_once_with(
        42,
        "photo-id",
        caption="preview",
        reply_markup="kb",
    )
    old.delete.assert_awaited_once()
    remember.assert_awaited_once_with(42, 7, 200)


def test_native_button_style_has_stock_pyrogram_fallback():
    from app.utils import button_style
    from app.utils import ui
    from pyrogram.enums import ButtonStyle

    def _button(label, **kwargs):
        if "style" in kwargs:
            raise TypeError("style unsupported")
        return SimpleNamespace(text=label, **kwargs)

    with patch.object(button_style, "InlineKeyboardButton", side_effect=_button):
        button = ui.compatible_inline_button(
            "Confirm",
            callback_data="confirm",
            style=ButtonStyle.SUCCESS,
        )

    assert button.text == "Confirm"
    assert button.callback_data == "confirm"


@pytest.mark.asyncio
async def test_private_callback_adapter_switches_text_edit_to_caption():
    from app.utils.telegram_message import install_private_callback_edit_adapter

    msg = _message(caption="media")
    msg.edit_text = AsyncMock(side_effect=RuntimeError("wrong API"))
    original_caption = msg.edit_caption
    query = _query(msg)
    client = SimpleNamespace(send_message=AsyncMock())

    restore = install_private_callback_edit_adapter(client, query)
    try:
        await msg.edit_text(
            "updated",
            reply_markup="kb",
            disable_web_page_preview=True,
        )
    finally:
        restore()

    original_caption.assert_awaited_once_with("updated", reply_markup="kb")
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_private_callback_adapter_sends_only_one_replacement():
    from app.utils.telegram_message import install_private_callback_edit_adapter

    msg = _message()
    msg.edit_text = AsyncMock(side_effect=RuntimeError("deleted"))
    msg.edit_caption = AsyncMock(side_effect=RuntimeError("deleted"))
    sent = _message(text="replacement")
    sent.id = 200
    client = SimpleNamespace(send_message=AsyncMock(return_value=sent))
    query = _query(msg)

    with patch(
        "app.services.panel_message_service.remember_panel_message",
        new_callable=AsyncMock,
    ):
        restore = install_private_callback_edit_adapter(client, query)
        try:
            await msg.edit_text("first", reply_markup="kb")
            await query.message.edit_text("second", reply_markup="kb2")
        finally:
            restore()

    client.send_message.assert_awaited_once()
    msg.delete.assert_awaited_once()
    sent.edit_text.assert_awaited_once_with("second", reply_markup="kb2")


@pytest.mark.asyncio
async def test_dev_validated_ask_retries_then_returns_one_valid_value():
    from app.handlers import dev_panel
    from app.utils.ask_result import AskResult

    invalid = SimpleNamespace(text="bad")
    valid = SimpleNamespace(text="12")
    ask_once = AsyncMock(
        side_effect=[
            AskResult(message=invalid),
            AskResult(message=valid),
        ]
    )
    with patch.object(dev_panel, "_ask", ask_once):
        result, value = await dev_panel._ask_value(
            SimpleNamespace(),
            42,
            "ask.amount_days",
            lambda message: int(message.text) if message.text.isdigit() else None,
            "invalid",
            user_id=7,
            return_to=dev_panel.TOKEN_DEV_SETTINGS,
        )

    assert result.message is valid
    assert value == 12
    assert ask_once.await_count == 2
    assert "invalid" in ask_once.await_args_list[1].kwargs["prompt_text"]


@pytest.mark.asyncio
async def test_install_policy_fee_callback_is_not_shadowed_by_removed_surface():
    from app.handlers import dev_panel
    from app.utils.ui import CB
    from pyrogram.enums import ChatType
    from pyrogram.types import CallbackQuery, Chat, Message, User

    class _Bot:
        def __init__(self):
            self.callbacks = []

        def on_callback_query(self, flt, group=0):
            def deco(fn):
                self.callbacks.append((fn, flt, group))
                return fn

            return deco

    bot = _Bot()
    dev_panel.register(bot, None)
    query = CallbackQuery(
        id="fee-query",
        data=CB["DEV_INSTALL_POLICY_FEE_GROUP"],
        from_user=User(id=123456789),
        chat_instance="fee-chat",
        message=Message(
            id=1,
            chat=Chat(id=123456789, type=ChatType.PRIVATE),
        ),
    )
    removed = next(item for item in bot.callbacks if item[0].__name__ == "dev_removed_financial_surface")
    fee = next(item for item in bot.callbacks if item[0].__name__ == "dev_install_policy_fee_group")

    assert await removed[1](SimpleNamespace(), query) is False
    assert await fee[1](SimpleNamespace(), query) is True


@pytest.mark.asyncio
async def test_dev_ask_deletes_scalar_reply_and_does_not_send_completion():
    from app.handlers import dev_panel

    response = SimpleNamespace(text="123", delete=AsyncMock())
    client = SimpleNamespace(send_message=AsyncMock())
    with (
        patch.object(
            dev_panel,
            "prompt_for_panel_input",
            new_callable=AsyncMock,
            return_value=response,
        ),
        patch.object(
            dev_panel,
            "safe_stop_listening",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch.object(dev_panel, "remember_return_token", new_callable=AsyncMock),
    ):
        result = await dev_panel._ask(
            client,
            42,
            "ask.rate_value",
            user_id=7,
            return_to=dev_panel.TOKEN_DEV_RATES,
        )

    assert result.message is response
    response.delete.assert_awaited_once()
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_answer_callback_safe_swallows_errors():
    from app.utils.telegram_message import answer_callback_safe

    query = _query()
    query.answer = AsyncMock(side_effect=RuntimeError("expired"))
    await answer_callback_safe(query)
    query.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_dev_listener_stopped_stays_quiet_for_callback_owned_redraw():
    from app.handlers import dev_panel
    from pyromod.exceptions import ListenerStopped

    client = SimpleNamespace(ask=AsyncMock(side_effect=ListenerStopped()))
    with patch.object(dev_panel, "deliver_ask_outcome", new_callable=AsyncMock) as deliver, patch.object(
        dev_panel, "safe_stop_listening", new_callable=AsyncMock, return_value=True,
    ), patch.object(dev_panel, "remember_return_token", new_callable=AsyncMock):
        result = await dev_panel._ask(
            client, 42, "ask.rate_value", user_id=7, return_to=dev_panel.TOKEN_DEV_RATES,
        )
    assert result.abort_reason == "listener_stopped"
    deliver.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_command_reply_still_allowed_for_initial_panel():
    """Command entry may send; this test only documents edit-first is callback-scoped."""
    from app.handlers.group_panel import _reply_group_panel

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=9),
        chat=SimpleNamespace(id=-1001, type=SimpleNamespace(value="supergroup")),
        reply=AsyncMock(return_value=SimpleNamespace(id=1)),
    )
    with patch(
        "app.handlers.group_panel._guard_group_chat_settings",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "app.handlers.group_panel.remember_panel_from_message",
        new_callable=AsyncMock,
    ):
        await _reply_group_panel(message)
    message.reply.assert_awaited_once()
