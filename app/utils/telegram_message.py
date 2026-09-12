"""Safe Telegram message edit/answer helpers for panel navigation UX."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from pyrogram.types import CallbackQuery, Message

logger = logging.getLogger(__name__)


def is_message_not_modified(exc: Exception) -> bool:
    """Return True when Telegram reports identical edit content."""
    return (
        "MessageNotModified" in type(exc).__name__
        or "MESSAGE_NOT_MODIFIED" in str(exc)
    )


def install_private_callback_edit_adapter(
    client: Any,
    query: CallbackQuery,
) -> Callable[[], None]:
    """Make direct private callback edits panel-safe for one handler run.

    Older handlers still call ``query.message.edit_text`` directly. For a
    media-backed start/admin panel that method is the wrong Telegram API. This
    adapter preserves the direct-call contract while trying the matching
    caption API and, only if both edits fail, sending one controlled
    replacement and deleting the stale panel.

    Returns a restoration callback for the original message methods.
    """
    message = getattr(query, "message", None)
    chat = getattr(message, "chat", None) if message is not None else None
    chat_type = getattr(chat, "type", None)
    chat_type = getattr(chat_type, "value", chat_type)
    if message is None or chat_type != "private":
        return lambda: None

    original_edit_text = getattr(message, "edit_text", None)
    original_edit_caption = getattr(message, "edit_caption", None)
    if not callable(original_edit_text) and not callable(original_edit_caption):
        return lambda: None

    message_dict = getattr(message, "__dict__", {})
    had_own_text = "edit_text" in message_dict
    had_own_caption = "edit_caption" in message_dict
    current_message: list[Any] = [message]
    replacement_sent = False

    def _caption_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
        converted = dict(kwargs)
        converted.pop("disable_web_page_preview", None)
        converted.pop("link_preview_options", None)
        if "entities" in converted and "caption_entities" not in converted:
            converted["caption_entities"] = converted.pop("entities")
        return converted

    def _text_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
        converted = dict(kwargs)
        if "caption_entities" in converted and "entities" not in converted:
            converted["entities"] = converted.pop("caption_entities")
        return converted

    def _send_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "parse_mode",
            "entities",
            "link_preview_options",
            "reply_markup",
            "disable_web_page_preview",
        }
        converted = _text_kwargs(kwargs)
        return {key: value for key, value in converted.items() if key in allowed}

    async def _remember(target: Any) -> None:
        chat_id = getattr(getattr(target, "chat", None), "id", None)
        message_id = getattr(target, "id", None) or getattr(target, "message_id", None)
        user_id = getattr(getattr(query, "from_user", None), "id", None)
        if not all(isinstance(value, int) for value in (chat_id, message_id, user_id)):
            return
        from app.services.panel_message_service import remember_panel_message

        await remember_panel_message(chat_id, user_id, message_id)

    async def _edit(
        content: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        *,
        prefer_caption: bool,
    ) -> Any:
        nonlocal replacement_sent
        target = current_message[0]

        if target is not message:
            primary_name = "edit_caption" if prefer_caption else "edit_text"
            secondary_name = "edit_text" if prefer_caption else "edit_caption"
            primary = getattr(target, primary_name, None)
            secondary = getattr(target, secondary_name, None)
            first_error: Exception | None = None
            if callable(primary):
                try:
                    call_kwargs = (
                        _caption_kwargs(kwargs)
                        if primary_name == "edit_caption"
                        else _text_kwargs(kwargs)
                    )
                    result = await primary(content, *args, **call_kwargs)
                    await _remember(target)
                    return result
                except Exception as exc:
                    if is_message_not_modified(exc):
                        return target
                    first_error = exc
            if callable(secondary):
                try:
                    call_kwargs = (
                        _caption_kwargs(kwargs)
                        if secondary_name == "edit_caption"
                        else _text_kwargs(kwargs)
                    )
                    result = await secondary(content, *args, **call_kwargs)
                    await _remember(target)
                    return result
                except Exception as exc:
                    if is_message_not_modified(exc):
                        return target
                    if first_error is None:
                        first_error = exc
            if first_error is not None:
                raise first_error
            raise RuntimeError("replacement panel is not editable")

        ordered = (
            (
                (original_edit_caption, _caption_kwargs(kwargs)),
                (original_edit_text, _text_kwargs(kwargs)),
            )
            if prefer_caption
            else (
                (original_edit_text, _text_kwargs(kwargs)),
                (original_edit_caption, _caption_kwargs(kwargs)),
            )
        )
        first_error: Exception | None = None
        for edit_method, call_kwargs in ordered:
            if not callable(edit_method):
                continue
            try:
                result = await edit_method(content, *args, **call_kwargs)
                await _remember(message)
                return result
            except Exception as exc:
                if is_message_not_modified(exc):
                    await _remember(message)
                    return message
                if first_error is None:
                    first_error = exc

        if replacement_sent:
            if first_error is not None:
                raise first_error
            raise RuntimeError("callback panel replacement already attempted")

        replacement_sent = True
        send_message = getattr(client, "send_message", None)
        chat_id = getattr(chat, "id", None)
        if not callable(send_message) or chat_id is None:
            if first_error is not None:
                raise first_error
            raise RuntimeError("callback panel cannot be edited or replaced")

        try:
            sent = await send_message(
                chat_id,
                content,
                **_send_kwargs(kwargs),
            )
        except Exception:
            if first_error is not None:
                raise first_error
            raise

        current_message[0] = sent
        try:
            query.message = sent
        except Exception:
            pass
        await _remember(sent)
        try:
            await message.delete()
        except Exception:
            logger.debug("private callback stale panel delete failed", exc_info=True)
        return sent

    async def _edit_text(content: str, *args: Any, **kwargs: Any) -> Any:
        return await _edit(content, args, kwargs, prefer_caption=False)

    async def _edit_caption(content: str, *args: Any, **kwargs: Any) -> Any:
        return await _edit(content, args, kwargs, prefer_caption=True)

    try:
        message.edit_text = _edit_text
        message.edit_caption = _edit_caption
    except Exception:
        return lambda: None

    def _restore() -> None:
        try:
            if had_own_text:
                message.edit_text = original_edit_text
            else:
                delattr(message, "edit_text")
        except Exception:
            pass
        try:
            if had_own_caption:
                message.edit_caption = original_edit_caption
            else:
                delattr(message, "edit_caption")
        except Exception:
            pass

    return _restore


async def safe_edit_message(
    message: Message,
    text: str,
    *,
    reply_markup: Any = None,
    parse_mode: str | None = None,
    disable_web_page_preview: bool = True,
) -> bool:
    """Edit a text or caption message; treat MessageNotModified as success."""
    text_kwargs: dict[str, Any] = {"reply_markup": reply_markup}
    caption_kwargs: dict[str, Any] = {"reply_markup": reply_markup}
    if parse_mode is not None:
        text_kwargs["parse_mode"] = parse_mode
        caption_kwargs["parse_mode"] = parse_mode
    if disable_web_page_preview:
        # Kurigram's Message.edit_caption() does not accept this text-only
        # argument. Keeping separate kwargs prevents a local TypeError from
        # masking a valid caption edit.
        text_kwargs["disable_web_page_preview"] = disable_web_page_preview

    try:
        if getattr(message, "text", None) is not None:
            await message.edit_text(text, **text_kwargs)
            return True
    except Exception as exc:
        if is_message_not_modified(exc):
            return True
        logger.debug("safe_edit_message edit_text failed: %s", type(exc).__name__)

    try:
        if getattr(message, "caption", None) is not None:
            await message.edit_caption(text, **caption_kwargs)
            return True
    except Exception as exc:
        if is_message_not_modified(exc):
            return True
        logger.debug("safe_edit_message edit_caption failed: %s", type(exc).__name__)

    try:
        await message.edit_text(text, **text_kwargs)
        return True
    except Exception as exc:
        if is_message_not_modified(exc):
            return True
        logger.debug("safe_edit_message edit_text fallback failed: %s", type(exc).__name__)

    try:
        await message.edit_caption(text, **caption_kwargs)
        return True
    except Exception as exc:
        if is_message_not_modified(exc):
            return True
        logger.debug("safe_edit_message edit_caption fallback failed: %s", type(exc).__name__)

    # A markup-only edit does not render the requested text. Reporting it as a
    # successful panel redraw leaves stale content visible and suppresses the
    # controlled send fallback.
    return False


async def safe_edit_or_send(
    client: Any,
    message: Message | None,
    text: str,
    *,
    reply_markup: Any = None,
    parse_mode: str | None = None,
    fallback_chat_id: int | None = None,
    disable_web_page_preview: bool = True,
    attempt_edit: bool = True,
) -> Message | None:
    """Edit the given message when possible; send at most one fallback message."""
    if message is not None and attempt_edit:
        if await safe_edit_message(
            message,
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        ):
            return message

    chat_id = fallback_chat_id
    if chat_id is None and message is not None:
        chat = getattr(message, "chat", None)
        chat_id = getattr(chat, "id", None)

    if chat_id is None:
        logger.warning("safe_edit_or_send: no editable message and no chat_id")
        return None

    send_message = getattr(client, "send_message", None)
    if not callable(send_message):
        return None

    kwargs: dict[str, Any] = {"reply_markup": reply_markup}
    if parse_mode is not None:
        kwargs["parse_mode"] = parse_mode
    if disable_web_page_preview:
        kwargs["disable_web_page_preview"] = disable_web_page_preview

    try:
        sent = await send_message(chat_id, text, **kwargs)
    except Exception as exc:
        logger.debug("safe_edit_or_send fallback send failed: %s", type(exc).__name__)
        return None

    if message is not None:
        try:
            await message.delete()
        except Exception as exc:
            logger.debug("safe_edit_or_send stale delete failed: %s", type(exc).__name__)

    return sent


async def answer_callback_safe(
    query: CallbackQuery,
    text: str | None = None,
    *,
    show_alert: bool = False,
) -> None:
    """Answer a callback query without raising into handlers."""
    try:
        if text is None and not show_alert:
            await query.answer()
        else:
            await query.answer(text, show_alert=show_alert)
    except Exception as exc:
        logger.debug("answer_callback_safe failed: %s", type(exc).__name__)
