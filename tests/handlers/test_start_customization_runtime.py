from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.handlers import callbacks, start
from app.services import start_customization_runtime as runtime
from app.services import start_customization_service as svc


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


def _message(user_id: int = 900003, message_id: int = 501):
    return SimpleNamespace(
        id=message_id,
        from_user=SimpleNamespace(id=user_id, first_name="User", username="user"),
        chat=SimpleNamespace(id=user_id),
        reply=AsyncMock(),
        stop_propagation=MagicMock(),
    )


def _query(data: str = "start:cat:ability"):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=900003, first_name="User"),
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=900003, type=SimpleNamespace(value="private")),
            reply=AsyncMock(),
            edit_text=AsyncMock(),
        ),
    )


def _item(**overrides):
    values = {
        "id": 1,
        "category": svc.CATEGORY_START,
        "source_scope": "global",
        "send_strategy": "send_text",
        "text": "Configured start",
    }
    values.update(overrides)
    return svc.MessageItemDescriptor(**values)


def _rendered_menu() -> svc.RenderedStartMenu:
    button = svc.StartButtonDescriptor(
        slot_index=1,
        slot_key="purchase",
        label="Buy",
        base_label="Buy",
        behavior="url",
        url="https://t.me/creator",
    )
    return svc.RenderedStartMenu(
        style_mode=svc.STYLE_SIMPLE,
        source_scope="default",
        rows=((button,),),
        uses_legacy_fallback=True,
    )


@pytest.mark.asyncio
async def test_build_regular_start_payload_resolves_owner_menu_and_start_item():
    item = _item()
    client = SimpleNamespace()

    with (
        patch(
            "app.services.start_customization_runtime.resolve_single_active_owner_user_id",
            AsyncMock(return_value=42),
        ) as resolve_owner,
        patch(
            "app.services.start_customization_runtime.get_start_welcome_text",
            AsyncMock(return_value="welcome"),
        ) as welcome,
        patch(
            "app.services.start_customization_runtime.build_start_links",
            AsyncMock(return_value={"creator": "https://t.me/creator"}),
        ) as links,
        patch(
            "app.services.start_customization_runtime.start_custom.render_start_menu",
            AsyncMock(return_value=_rendered_menu()),
        ) as render_menu,
        patch(
            "app.services.start_customization_runtime.start_custom.get_random_message_item",
            AsyncMock(return_value=item),
        ) as get_item,
    ):
        result_item, fallback_text, kb = await runtime.build_regular_start_payload(
            client,
            user_id=900003,
            first_name="User",
            lang="en",
        )

    assert result_item == item
    assert fallback_text == "welcome"
    assert kb.inline_keyboard[0][0].url == "https://t.me/creator"
    resolve_owner.assert_awaited_once()
    welcome.assert_awaited_once()
    links.assert_awaited_once_with(client, owner_user_id=42)
    render_menu.assert_awaited_once()
    get_item.assert_awaited_once_with(
        category=svc.CATEGORY_START,
        owner_user_id=42,
        chat_id=None,
    )


@pytest.mark.asyncio
async def test_deliver_message_item_copies_source_with_keyboard():
    client = SimpleNamespace(copy_message=AsyncMock())
    message = _message()
    item = _item(
        send_strategy="copy_source",
        source_chat_id=-1001,
        source_message_id=55,
        text=None,
    )

    result = await runtime.deliver_message_item(
        client,
        chat_id=900003,
        anchor_message=message,
        item=item,
        fallback_text="fallback",
        reply_markup="kb",
    )

    assert result == "copied_source"
    client.copy_message.assert_awaited_once_with(
        900003,
        -1001,
        55,
        reply_markup="kb",
        reply_to_message_id=501,
    )
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliver_message_item_falls_back_when_copy_fails_without_snapshot():
    client = SimpleNamespace(copy_message=AsyncMock(side_effect=RuntimeError("gone")))
    message = _message()
    item = _item(
        send_strategy="copy_source",
        source_chat_id=-1001,
        source_message_id=55,
        text=None,
    )

    result = await runtime.deliver_message_item(
        client,
        chat_id=900003,
        anchor_message=message,
        item=item,
        fallback_text="fallback",
        reply_markup="kb",
    )

    assert result == "fallback_after_copy_failed"
    message.reply.assert_awaited_once_with("fallback", reply_markup="kb")


@pytest.mark.asyncio
async def test_deliver_message_item_uses_snapshot_when_copy_fails():
    client = SimpleNamespace(copy_message=AsyncMock(side_effect=RuntimeError("gone")))
    message = _message()
    item = _item(
        send_strategy="copy_source",
        source_chat_id=-1001,
        source_message_id=55,
        text="saved snapshot",
    )

    result = await runtime.deliver_message_item(
        client,
        chat_id=900003,
        anchor_message=message,
        item=item,
        fallback_text="fallback",
        reply_markup="kb",
    )

    assert result == "sent_snapshot_fallback_after_copy_failed"
    message.reply.assert_awaited_once_with("saved snapshot", reply_markup="kb")


@pytest.mark.asyncio
async def test_deliver_message_item_sends_media_when_copy_fails():
    client = SimpleNamespace(
        copy_message=AsyncMock(side_effect=RuntimeError("gone")),
        send_photo=AsyncMock(),
    )
    message = _message()
    item = _item(
        send_strategy="copy_source",
        source_chat_id=-1001,
        source_message_id=55,
        media_type="photo",
        media_file_id="photo-file-id",
        caption="saved caption",
        text=None,
    )

    result = await runtime.deliver_message_item(
        client,
        chat_id=900003,
        anchor_message=message,
        item=item,
        fallback_text="fallback",
        reply_markup="kb",
    )

    assert result == "sent_media_fallback_after_copy_failed"
    client.send_photo.assert_awaited_once_with(
        900003,
        "photo-file-id",
        reply_markup="kb",
        reply_to_message_id=501,
        caption="saved caption",
        caption_entities=None,
    )
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliver_message_item_sends_media_file_id():
    client = SimpleNamespace(send_photo=AsyncMock())
    message = _message()
    item = _item(
        send_strategy="send_media",
        media_type="photo",
        media_file_id="photo-file-id",
        caption="caption",
        text=None,
    )

    result = await runtime.deliver_message_item(
        client,
        chat_id=900003,
        anchor_message=message,
        item=item,
        fallback_text="fallback",
        reply_markup="kb",
    )

    assert result == "sent_media"
    client.send_photo.assert_awaited_once_with(
        900003,
        "photo-file-id",
        reply_markup="kb",
        reply_to_message_id=501,
        caption="caption",
        caption_entities=None,
    )
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliver_message_item_sends_video_note():
    client = SimpleNamespace(send_video_note=AsyncMock())
    message = _message()
    item = _item(
        send_strategy="send_media",
        media_type="video_note",
        media_file_id="video-note-id",
        text=None,
    )

    result = await runtime.deliver_message_item(
        client,
        chat_id=900003,
        anchor_message=message,
        item=item,
        fallback_text="fallback",
        reply_markup="kb",
    )

    assert result == "sent_media"
    client.send_video_note.assert_awaited_once_with(
        900003,
        "video-note-id",
        reply_markup="kb",
        reply_to_message_id=501,
    )


@pytest.mark.asyncio
async def test_start_handler_regular_user_uses_custom_runtime():
    bot = _RecorderBot()
    start.register(bot, None)
    handler = _handler_by_name(bot.message_handlers, "start_handler")
    client = SimpleNamespace()
    message = _message()

    with (
        patch("app.handlers.start.check_force_join", AsyncMock(return_value=True)),
        patch("app.handlers.start.track_event", AsyncMock()),
        patch("app.handlers.start.user_repo.upsert_user", AsyncMock()),
        patch("app.handlers.start.detect_private_role", AsyncMock(return_value="regular")),
        patch("app.handlers.start.is_bot_enabled", AsyncMock(return_value=True)),
        patch(
            "app.handlers.start.start_runtime.deliver_regular_start",
            AsyncMock(return_value="sent_text"),
        ) as deliver,
    ):
        await handler(client, message)

    deliver.assert_awaited_once()
    message.reply.assert_not_awaited()
    message.stop_propagation.assert_called_once()


@pytest.mark.asyncio
async def test_start_handler_privileged_user_uses_regular_home_with_management_entry():
    bot = _RecorderBot()
    start.register(bot, None)
    handler = _handler_by_name(bot.message_handlers, "start_handler")
    client = SimpleNamespace()
    message = _message(user_id=123456789)

    with (
        patch("app.handlers.start.check_force_join", AsyncMock(return_value=True)),
        patch("app.handlers.start.track_event", AsyncMock()),
        patch("app.handlers.start.user_repo.upsert_user", AsyncMock()),
        patch("app.handlers.start.detect_private_role", AsyncMock(return_value="developer")),
        patch("app.handlers.start.NotificationService.notify_bot_start", AsyncMock()),
        patch(
            "app.handlers.start.start_runtime.deliver_regular_start",
            AsyncMock(),
        ) as deliver,
    ):
        await handler(client, message)

    deliver.assert_awaited_once_with(
        client,
        message,
        user_id=123456789,
        first_name="User",
        lang="fa",
        management_role="developer",
    )
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_category_callback_renders_configured_content():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "start_category_content")
    query = _query("start:cat:ability")
    client = SimpleNamespace()
    item = _item(category=svc.CATEGORY_ABILITY, text="Ability")

    with (
        patch(
            "app.handlers.callbacks.resolve_single_active_owner_user_id",
            AsyncMock(return_value=42),
        ),
        patch(
            "app.handlers.callbacks.start_custom.get_random_message_item",
            AsyncMock(return_value=item),
        ) as get_item,
        patch(
            "app.handlers.callbacks.start_runtime.deliver_message_item",
            AsyncMock(return_value="sent_text"),
        ) as deliver,
    ):
        await handler(client, query)

    query.answer.assert_awaited_once()
    get_item.assert_awaited_once_with(
        category=svc.CATEGORY_ABILITY,
        owner_user_id=42,
        chat_id=None,
    )
    deliver.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_category_test_callback_denies_when_policy_hides_test():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "start_category_content")
    query = _query("start:cat:test")

    with patch(
        "app.handlers.callbacks.start_custom.is_test_category_exposed",
        AsyncMock(return_value=False),
    ):
        await handler(SimpleNamespace(), query)

    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs["show_alert"] is True


@pytest.mark.asyncio
async def test_start_category_malformed_callback_alerts():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "start_category_malformed")
    query = _query("start:cat:")

    await handler(SimpleNamespace(), query)

    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs["show_alert"] is True


@pytest.mark.asyncio
async def test_build_private_root_payload_uses_custom_menu_for_regular_user():
    from app.services.panel_router import build_private_root_payload
    client = SimpleNamespace()
    
    with (
        patch("app.services.panel_router.detect_private_role", AsyncMock(return_value="regular")),
        patch("app.services.panel_router.is_bot_enabled", AsyncMock(return_value=True)),
        patch("app.services.panel_router.get_start_welcome_text", AsyncMock(return_value="welcome")),
        patch("app.services.panel_router.build_start_links", AsyncMock(return_value={"creator": "https://creator"})),
        patch("app.services.bot_settings_service.resolve_single_active_owner_user_id", AsyncMock(return_value=42)),
        patch("app.services.start_customization_service.render_start_menu", AsyncMock(return_value=svc.RenderedStartMenu(
            style_mode=svc.STYLE_SIMPLE,
            source_scope="global",
            rows=((svc.StartButtonDescriptor(
                slot_index=1,
                slot_key="purchase",
                label="Custom Buy",
                base_label="Buy",
                behavior="url",
                url="https://creator",
            ),),),
            uses_legacy_fallback=False,
        ))),
    ):
        text, kb = await build_private_root_payload(
            client,
            900003,
            "User",
            lang="en",
            include_welcome=True,
        )
    
    assert text == "welcome"
    assert kb is not None
    assert kb.inline_keyboard[0][0].text == "Custom Buy"
