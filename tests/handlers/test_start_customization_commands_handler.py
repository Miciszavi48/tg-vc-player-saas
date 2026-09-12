from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.settings import settings
from app.handlers import start_customization_commands as handler_mod


class _RecorderBot:
    def __init__(self) -> None:
        self.message_handlers: list = []

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.message_handlers.append(fn)
            return fn

        return _decorator


def _handler():
    bot = _RecorderBot()
    handler_mod.register(bot, None)
    return bot.message_handlers[0]


def _chat(chat_id: int, chat_type: str = "private"):
    return SimpleNamespace(
        id=chat_id,
        title=f"Chat {chat_id}",
        type=SimpleNamespace(value=chat_type),
    )


def _message(
    text: str,
    *,
    user_id: int = settings.DEVELOPER_ID,
    chat_id: int | None = None,
    chat_type: str = "private",
    reply_to_message=None,
):
    chat_id = user_id if chat_id is None else chat_id
    return SimpleNamespace(
        chat=_chat(chat_id, chat_type),
        from_user=SimpleNamespace(id=user_id, username=f"user{user_id}"),
        text=text,
        caption=None,
        entities=[],
        reply_to_message=reply_to_message,
        reply=AsyncMock(),
        reply_text=AsyncMock(),
        stop_propagation=MagicMock(),
        continue_propagation=MagicMock(),
    )


def _reply_text(
    text: str,
    *,
    chat_id: int = 100,
    message_id: int = 55,
    entities=None,
):
    return SimpleNamespace(
        chat=_chat(chat_id, "private"),
        id=message_id,
        text=text,
        caption=None,
        entities=entities or [],
        caption_entities=None,
        photo=None,
        video=None,
        document=None,
        audio=None,
        animation=None,
        voice=None,
        video_note=None,
        sticker=None,
    )


def _reply_photo(caption: str = "caption"):
    return SimpleNamespace(
        chat=_chat(200, "supergroup"),
        id=77,
        text=None,
        caption=caption,
        entities=[],
        caption_entities=[],
        photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="photo_big")],
        video=None,
        document=None,
        audio=None,
        animation=None,
        voice=None,
        video_note=None,
        sticker=None,
    )


@pytest.mark.asyncio
async def test_developer_private_add_start_message_uses_global_scope(monkeypatch):
    add_mock = AsyncMock()
    monkeypatch.setattr(handler_mod.start_custom, "add_message_pool_item", add_mock)
    monkeypatch.setattr(
        handler_mod.start_custom,
        "is_test_category_exposed",
        AsyncMock(return_value=True),
    )
    msg = _message("addstartMsg", reply_to_message=_reply_text("hello start"))

    await _handler()(AsyncMock(), msg)

    kwargs = add_mock.await_args.kwargs
    assert kwargs["scope_type"] == "global"
    assert kwargs["owner_user_id"] is None
    assert kwargs["category"] == "start"
    assert kwargs["actor_user_id"] == settings.DEVELOPER_ID
    assert kwargs["text"] == "hello start"
    assert kwargs["source_message_id"] == 55
    assert "موفقیت" in msg.reply.await_args.args[0]
    assert "حذف نکنید" in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_owner_private_add_uses_owner_scope(monkeypatch):
    owner_id = 9001
    add_mock = AsyncMock()
    monkeypatch.setattr(handler_mod.user_repo, "is_owner", AsyncMock(return_value=True))
    monkeypatch.setattr(
        handler_mod,
        "resolve_single_active_owner_user_id",
        AsyncMock(return_value=owner_id),
    )
    monkeypatch.setattr(handler_mod.start_custom, "add_message_pool_item", add_mock)
    msg = _message(
        "افزودن پیام امکانات",
        user_id=owner_id,
        reply_to_message=_reply_text("ability"),
    )

    await _handler()(AsyncMock(), msg)

    kwargs = add_mock.await_args.kwargs
    assert kwargs["scope_type"] == "owner"
    assert kwargs["owner_user_id"] == owner_id
    assert kwargs["category"] == "ability"


@pytest.mark.asyncio
async def test_regular_user_denied_without_mutation(monkeypatch):
    add_mock = AsyncMock()
    monkeypatch.setattr(handler_mod.user_repo, "is_owner", AsyncMock(return_value=False))
    monkeypatch.setattr(handler_mod.start_custom, "add_message_pool_item", add_mock)
    msg = _message(
        "addstartMsg",
        user_id=9911,
        reply_to_message=_reply_text("body"),
    )

    await _handler()(AsyncMock(), msg)

    add_mock.assert_not_awaited()
    assert "دسترسی" in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_non_designated_owner_cannot_write_unread_scope(monkeypatch):
    owner_id = 9002
    add_mock = AsyncMock()
    monkeypatch.setattr(handler_mod.user_repo, "is_owner", AsyncMock(return_value=True))
    monkeypatch.setattr(
        handler_mod,
        "resolve_single_active_owner_user_id",
        AsyncMock(return_value=9001),
    )
    monkeypatch.setattr(handler_mod.start_custom, "add_message_pool_item", add_mock)
    msg = _message(
        "addstartMsg",
        user_id=owner_id,
        reply_to_message=_reply_text("body"),
    )

    await _handler()(AsyncMock(), msg)

    add_mock.assert_not_awaited()
    assert "یکتا" in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_sudo_denied_without_mutation(monkeypatch):
    add_mock = AsyncMock()
    monkeypatch.setattr(handler_mod.user_repo, "is_owner", AsyncMock(return_value=False))
    monkeypatch.setattr(handler_mod.user_repo, "is_sudo", AsyncMock(return_value=True))
    monkeypatch.setattr(handler_mod.start_custom, "add_message_pool_item", add_mock)
    msg = _message(
        "addstartMsg",
        user_id=9922,
        reply_to_message=_reply_text("body"),
    )

    await _handler()(AsyncMock(), msg)

    add_mock.assert_not_awaited()
    assert "دسترسی" in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_no_reply_rejected_for_add_message(monkeypatch):
    add_mock = AsyncMock()
    monkeypatch.setattr(handler_mod.start_custom, "add_message_pool_item", add_mock)
    msg = _message("addstartMsg")

    await _handler()(AsyncMock(), msg)

    add_mock.assert_not_awaited()
    assert "ریپلای" in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_invalid_message_payload_gets_validation_feedback(monkeypatch):
    add_mock = AsyncMock(side_effect=ValueError("message text is too long"))
    monkeypatch.setattr(handler_mod.start_custom, "add_message_pool_item", add_mock)
    msg = _message("addstartMsg", reply_to_message=_reply_text("body"))

    await _handler()(AsyncMock(), msg)

    add_mock.assert_awaited_once()
    assert "بیش از حد" in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_invalid_color_rejected_without_write(monkeypatch):
    color_mock = AsyncMock(side_effect=ValueError("bad color"))
    monkeypatch.setattr(handler_mod.start_custom, "set_button_color_sequence", color_mock)
    msg = _message(
        "addstart keycolor",
        reply_to_message=_reply_text("RGBNRGBT"),
    )

    await _handler()(AsyncMock(), msg)

    color_mock.assert_awaited_once()
    assert "رنگ" in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_color_sequence_success_explains_advanced_new_start(monkeypatch):
    color_mock = AsyncMock()
    monkeypatch.setattr(handler_mod.start_custom, "set_button_color_sequence", color_mock)
    msg = _message(
        "addstart keycolor",
        reply_to_message=_reply_text("RBGNNNNR"),
    )

    await _handler()(AsyncMock(), msg)

    kwargs = color_mock.await_args.kwargs
    assert kwargs["scope_type"] == "global"
    assert kwargs["owner_user_id"] is None
    assert kwargs["value"] == "RBGNNNNR"
    feedback = msg.reply.await_args.args[0]
    assert "پیشرفته" in feedback
    assert "/start" in feedback


@pytest.mark.asyncio
async def test_clean_color_restores_automatic_palette_with_clear_guidance(monkeypatch):
    clear_mock = AsyncMock(return_value=3)
    monkeypatch.setattr(
        handler_mod.start_custom,
        "clear_button_part_sequence",
        clear_mock,
    )
    msg = _message("cleanstart keycolor")

    await _handler()(AsyncMock(), msg)

    kwargs = clear_mock.await_args.kwargs
    assert kwargs["scope_type"] == "global"
    assert kwargs["owner_user_id"] is None
    assert kwargs["part"] == "color"
    feedback = msg.reply.await_args.args[0]
    assert "RBGNNNNR" in feedback
    assert "NNNNNNNN" in feedback


@pytest.mark.asyncio
async def test_keytext_line_count_mismatch_rejected(monkeypatch):
    text_mock = AsyncMock(side_effect=ValueError("bad lines"))
    monkeypatch.setattr(handler_mod.start_custom, "set_button_text_lines", text_mock)
    msg = _message(
        "addstart keytext",
        reply_to_message=_reply_text("one\ntwo"),
    )

    await _handler()(AsyncMock(), msg)

    text_mock.assert_awaited_once()
    assert "۸ خط" in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_media_reply_payload_is_passed_to_service(monkeypatch):
    add_mock = AsyncMock()
    monkeypatch.setattr(handler_mod.start_custom, "add_message_pool_item", add_mock)
    msg = _message("addnoteMsg", reply_to_message=_reply_photo("photo caption"))

    await _handler()(AsyncMock(), msg)

    kwargs = add_mock.await_args.kwargs
    assert kwargs["category"] == "note"
    assert kwargs["message_type"] == "photo"
    assert kwargs["media_file_id"] == "photo_big"
    assert kwargs["caption"] == "photo caption"
    assert kwargs["source_chat_id"] == 200


@pytest.mark.asyncio
async def test_emoji_entity_metadata_passed_to_service(monkeypatch):
    entity = SimpleNamespace(
        type=SimpleNamespace(value="custom_emoji"),
        offset=0,
        length=2,
        custom_emoji_id="emoji-1",
    )
    emoji_mock = AsyncMock()
    monkeypatch.setattr(handler_mod.start_custom, "set_button_emoji_sequence", emoji_mock)
    msg = _message(
        "addstart keyemoji",
        reply_to_message=_reply_text("😀 😃 😄 😁 😆 😅 😂 🙂", entities=[entity]),
    )

    await _handler()(AsyncMock(), msg)

    kwargs = emoji_mock.await_args.kwargs
    assert kwargs["emoji_entities_json"] is not None
    assert "emoji-1" in kwargs["emoji_entities_json"]


@pytest.mark.asyncio
async def test_trusted_report_group_applies_to_current_single_owner(monkeypatch):
    add_mock = AsyncMock()
    monkeypatch.setattr(handler_mod, "resolve_log_channel_id", AsyncMock(return_value=-100700))
    monkeypatch.setattr(
        handler_mod,
        "resolve_single_active_owner_user_id",
        AsyncMock(return_value=9007),
    )
    monkeypatch.setattr(handler_mod.start_custom, "add_message_pool_item", add_mock)
    msg = _message(
        "addhistoryMsg",
        chat_id=-100700,
        chat_type="supergroup",
        reply_to_message=_reply_text("history"),
    )

    await _handler()(AsyncMock(), msg)

    kwargs = add_mock.await_args.kwargs
    assert kwargs["scope_type"] == "owner"
    assert kwargs["owner_user_id"] == 9007
    assert kwargs["category"] == "history"


@pytest.mark.asyncio
async def test_untrusted_group_denied(monkeypatch):
    add_mock = AsyncMock()
    monkeypatch.setattr(handler_mod, "resolve_log_channel_id", AsyncMock(return_value=-100700))
    monkeypatch.setattr(handler_mod.start_custom, "add_message_pool_item", add_mock)
    msg = _message(
        "addhistoryMsg",
        chat_id=-100701,
        chat_type="supergroup",
        reply_to_message=_reply_text("history"),
    )

    await _handler()(AsyncMock(), msg)

    add_mock.assert_not_awaited()
    assert "گروه گزارش" in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_regular_user_cannot_forge_report_group_authority(monkeypatch):
    add_mock = AsyncMock()
    monkeypatch.setattr(handler_mod, "resolve_log_channel_id", AsyncMock(return_value=-100700))
    monkeypatch.setattr(handler_mod.user_repo, "is_owner", AsyncMock(return_value=False))
    monkeypatch.setattr(handler_mod.start_custom, "add_message_pool_item", add_mock)
    msg = _message(
        "addhistoryMsg",
        user_id=99002,
        chat_id=-100700,
        chat_type="supergroup",
        reply_to_message=_reply_text("history"),
    )

    await _handler()(AsyncMock(), msg)

    add_mock.assert_not_awaited()
    assert "دسترسی" in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_fanout_partial_failure_is_reported(monkeypatch):
    targets = [
        handler_mod.StartCustomizationTarget("one", "owner", 1),
        handler_mod.StartCustomizationTarget("two", "owner", 2),
    ]
    add_mock = AsyncMock(side_effect=[None, RuntimeError("db down")])
    monkeypatch.setattr(
        handler_mod,
        "_resolve_targets",
        AsyncMock(return_value=(targets, None)),
    )
    monkeypatch.setattr(handler_mod.start_custom, "add_message_pool_item", add_mock)
    msg = _message("addstartMsg", reply_to_message=_reply_text("body"))

    await _handler()(AsyncMock(), msg)

    assert add_mock.await_count == 2
    assert "ناموفق" in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_addtest_requires_paid_policy_and_does_not_mutate_when_denied(monkeypatch):
    add_mock = AsyncMock()
    monkeypatch.setattr(
        handler_mod.start_custom,
        "is_test_category_exposed",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(handler_mod.start_custom, "add_message_pool_item", add_mock)
    msg = _message("addtestMsg", reply_to_message=_reply_text("test"))

    await _handler()(AsyncMock(), msg)

    add_mock.assert_not_awaited()
    assert "نصب پولی" in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_addtest_allowed_when_paid_policy_allows(monkeypatch):
    add_mock = AsyncMock()
    monkeypatch.setattr(
        handler_mod.start_custom,
        "is_test_category_exposed",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(handler_mod.start_custom, "add_message_pool_item", add_mock)
    msg = _message("addtestMsg", reply_to_message=_reply_text("test"))

    await _handler()(AsyncMock(), msg)

    assert add_mock.await_args.kwargs["category"] == "test"


@pytest.mark.asyncio
async def test_cleantest_allowed_even_when_test_policy_is_closed(monkeypatch):
    clear_mock = AsyncMock(return_value=1)
    policy_mock = AsyncMock(return_value=False)
    monkeypatch.setattr(handler_mod.start_custom, "is_test_category_exposed", policy_mock)
    monkeypatch.setattr(handler_mod.start_custom, "clear_category_pool", clear_mock)
    msg = _message("cleantestMsg")

    await _handler()(AsyncMock(), msg)

    policy_mock.assert_not_awaited()
    clear_mock.assert_awaited_once()
    assert clear_mock.await_args.kwargs["category"] == "test"
