"""Structured, secret-safe tracing helpers for callback query handling."""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any

from pyrogram.types import CallbackQuery

from app.utils.diagnostic_logging import (
    callback_data_prefix,
    redact_freeform_text,
    safe_exc_name,
)
from app.utils.telegram_message import safe_edit_message

logger = logging.getLogger("callback_trace")


@dataclass
class CallbackTrace:
    cbid: str
    data: str
    prefix: str
    callback_length: int
    user_id: int | None
    chat_id: int | None
    chat_type: str | None
    message_chat_id: int | None
    message_id: int | None
    started_at: float


def _query_data(query: CallbackQuery) -> str:
    return str(getattr(query, "data", "") or "")


def _query_chat(query: CallbackQuery):
    message = getattr(query, "message", None)
    return getattr(message, "chat", None) if message is not None else None


def _query_message_id(query: CallbackQuery) -> int | None:
    message = getattr(query, "message", None)
    if message is None:
        return None
    return getattr(message, "id", None) or getattr(message, "message_id", None)


def _chat_type_value(chat: Any) -> str | None:
    raw_type = getattr(chat, "type", None)
    return getattr(raw_type, "value", raw_type) if raw_type is not None else None


def _build_cbid(
    query: CallbackQuery, data: str, user_id: int | None, chat_id: int | None
) -> str:
    query_id = getattr(query, "id", None)
    if query_id or data or user_id or chat_id:
        source = f"{query_id or ''}:{data}:{user_id or ''}:{chat_id or ''}"
        return hashlib.blake2s(source.encode("utf-8"), digest_size=4).hexdigest()
    return secrets.token_hex(4)


def get_or_create_callback_trace(query: CallbackQuery) -> CallbackTrace:
    existing = getattr(query, "_musicbot_callback_trace", None)
    if isinstance(existing, CallbackTrace):
        return existing

    data = _query_data(query)
    chat = _query_chat(query)
    from_user = getattr(query, "from_user", None)
    user_id = getattr(from_user, "id", None)
    message_chat_id = getattr(chat, "id", None)
    trace = CallbackTrace(
        cbid=_build_cbid(query, data, user_id, message_chat_id),
        data=callback_data_prefix(data),
        prefix=data.split(":", 1)[0] if data else "",
        callback_length=len(data),
        user_id=user_id,
        chat_id=message_chat_id,
        chat_type=_chat_type_value(chat),
        message_chat_id=message_chat_id,
        message_id=_query_message_id(query),
        started_at=time.monotonic(),
    )
    try:
        query._musicbot_callback_trace = trace
    except Exception:
        pass
    return trace


def mark_route_seen(query: CallbackQuery) -> None:
    try:
        query._musicbot_callback_route_seen = True
    except Exception:
        pass


def _safe_value(value: Any) -> str | int | bool | None:
    if value is None:
        return None
    if isinstance(value, (int, bool)):
        return value
    return redact_freeform_text(str(value))[:240]


def _emit(
    event: str, query: CallbackQuery, *, level: str = "info", **fields: Any
) -> None:
    trace = get_or_create_callback_trace(query)
    base: dict[str, Any] = {
        "cbid": trace.cbid,
        "data": trace.data,
        "prefix": trace.prefix,
        "callback_length": trace.callback_length,
        "user_id": trace.user_id,
        "chat_id": trace.chat_id,
        "chat_type": trace.chat_type,
        "message_chat_id": trace.message_chat_id,
        "message_id": trace.message_id,
    }
    base.update(fields)
    parts = [event]
    for key, value in base.items():
        safe = _safe_value(value)
        if safe is None or safe == "":
            continue
        parts.append(f"{key}={safe}")
    message = " ".join(parts)
    if level == "debug":
        logger.debug(message)
    elif level == "warning":
        logger.warning(message)
    elif level == "error":
        logger.error(message)
    else:
        logger.info(message)


def trace_callback_event(
    event: str,
    query: CallbackQuery,
    *,
    level: str = "info",
    error: BaseException | None = None,
    **fields: Any,
) -> None:
    if error is not None:
        fields.setdefault("exception_class", safe_exc_name(error))
    _emit(event, query, level=level, **fields)


def trace_received(query: CallbackQuery) -> None:
    _emit("callback.received", query, route="pre_dispatch")


def trace_route(
    query: CallbackQuery,
    *,
    handler: str,
    module: str | None = None,
    group: int | None = None,
) -> None:
    _emit("callback.route", query, handler=handler, module=module, group=group)


def trace_handler_start(
    query: CallbackQuery,
    *,
    handler: str,
    module: str | None = None,
) -> None:
    _emit("callback.handler.start", query, handler=handler, module=module)


def trace_guard(
    query: CallbackQuery,
    *,
    guard: str,
    result: str,
    reason: str | None = None,
    handler: str | None = None,
) -> None:
    _emit(
        "callback.guard",
        query,
        guard=guard,
        result=result,
        reason=reason,
        handler=handler,
    )


def trace_answer(
    query: CallbackQuery,
    *,
    result: str,
    text_key: str | None = None,
    show_alert: bool | None = None,
    error: BaseException | None = None,
    source: str | None = None,
) -> None:
    if result == "ok":
        try:
            query._musicbot_callback_answer_trace_emitted = True
        except Exception:
            pass
    _emit(
        "callback.answer",
        query,
        result=result,
        text_key=text_key,
        show_alert=show_alert,
        source=source,
        exception_class=safe_exc_name(error) if error is not None else None,
        safe_message=redact_freeform_text(str(error))[:160]
        if error is not None
        else None,
    )


def trace_edit(
    query: CallbackQuery,
    *,
    result: str,
    handler: str | None = None,
    error: BaseException | None = None,
) -> None:
    event_by_result = {
        "edited": "callback.edit.success",
        "fallback_attempted": "callback.edit.fallback",
        "fallback_replied": "callback.edit.fallback",
        "fallback_sent": "callback.edit.fallback",
        "edit_failed": "callback.edit.fail",
        "reply_failed": "callback.edit.fail",
        "send_failed": "callback.edit.fail",
        "failed": "callback.edit.fail",
    }
    _emit(
        "callback.edit",
        query,
        result=result,
        handler=handler,
        exception_class=safe_exc_name(error) if error is not None else None,
        safe_message=redact_freeform_text(str(error))[:160]
        if error is not None
        else None,
    )
    canonical_event = event_by_result.get(result)
    if canonical_event is not None:
        _emit(
            canonical_event,
            query,
            level="warning" if canonical_event != "callback.edit.success" else "info",
            result=result,
            handler=handler,
            exception_class=safe_exc_name(error) if error is not None else None,
            safe_message=redact_freeform_text(str(error))[:160]
            if error is not None
            else None,
        )


def trace_helper_summary(
    query: CallbackQuery,
    *,
    helpers_total: int,
    active: int,
    disabled: int,
    quarantined: int,
    available: int,
) -> None:
    _emit(
        "callback.helper.summary",
        query,
        helpers_total=helpers_total,
        active=active,
        disabled=disabled,
        quarantined=quarantined,
        available=available,
    )


def trace_done(
    query: CallbackQuery,
    *,
    handler: str,
    module: str | None = None,
    result: str = "ok",
) -> None:
    trace = get_or_create_callback_trace(query)
    duration_ms = int((time.monotonic() - trace.started_at) * 1000)
    _emit(
        "callback.handler.done",
        query,
        handler=handler,
        module=module,
        result=result,
        duration_ms=duration_ms,
    )


def trace_failed(
    query: CallbackQuery,
    *,
    handler: str,
    error: BaseException,
    module: str | None = None,
) -> None:
    trace = get_or_create_callback_trace(query)
    duration_ms = int((time.monotonic() - trace.started_at) * 1000)
    _emit(
        "callback.handler.failed",
        query,
        handler=handler,
        module=module,
        exception_class=safe_exc_name(error),
        safe_message=redact_freeform_text(str(error))[:160],
        duration_ms=duration_ms,
    )


def trace_unhandled(query: CallbackQuery, *, reason: str) -> None:
    _emit(
        "callback.unhandled",
        query,
        route="post_dispatch_unhandled",
        handler="none",
        reason=reason,
    )


async def safe_answer_callback(
    query: CallbackQuery,
    text: str | None = None,
    *,
    show_alert: bool = False,
    text_key: str | None = None,
) -> None:
    if getattr(query, "_musicbot_callback_answered", False):
        trace_answer(
            query, result="skipped_duplicate", text_key=text_key, show_alert=show_alert
        )
        return
    try:
        if text is None:
            await query.answer()
        else:
            await query.answer(text, show_alert=show_alert)
        try:
            query._musicbot_callback_answered = True
        except Exception:
            pass
        if not getattr(query, "_musicbot_callback_answer_trace_emitted", False):
            trace_answer(query, result="ok", text_key=text_key, show_alert=show_alert)
    except Exception as exc:
        trace_answer(
            query,
            result="failed",
            text_key=text_key,
            show_alert=show_alert,
            error=exc,
        )


async def safe_edit_or_send_callback(
    client: Any,
    query: CallbackQuery,
    text: str,
    *,
    reply_markup: Any = None,
    handler: str | None = None,
    fallback_chat_id: int | None = None,
) -> str:
    message = getattr(query, "message", None)
    if message is not None:
        trace_callback_event(
            "callback.edit.start", query, handler=handler, target="callback_message"
        )
        try:
            if await safe_edit_message(message, text, reply_markup=reply_markup):
                trace_edit(query, result="edited", handler=handler)
                return "edited"
        except Exception as exc:
            trace_edit(query, result="edit_failed", handler=handler, error=exc)

        reply = getattr(message, "reply", None)
        if callable(reply):
            try:
                await reply(text, reply_markup=reply_markup)
                trace_edit(query, result="fallback_replied", handler=handler)
                return "fallback_sent"
            except Exception as exc:
                trace_edit(query, result="reply_failed", handler=handler, error=exc)

    chat_id = fallback_chat_id
    if chat_id is None and message is not None:
        chat = getattr(message, "chat", None)
        chat_id = getattr(chat, "id", None)

    send_message = getattr(client, "send_message", None) if client is not None else None
    if callable(send_message) and chat_id is not None:
        try:
            await send_message(chat_id, text, reply_markup=reply_markup)
            trace_edit(query, result="fallback_sent", handler=handler)
            return "fallback_sent"
        except Exception as exc:
            trace_edit(query, result="send_failed", handler=handler, error=exc)

    trace_edit(query, result="failed", handler=handler)
    return "failed"
