"""Shared AskResult type and no-silent abort helpers for pyromod ask flows."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pyrogram import Client, filters
from pyrogram.types import Message

from app.utils.i18n import t

logger = logging.getLogger(__name__)

_ABORT_MESSAGE_KEY = "texts_links.ask_cancelled_or_timeout"
_NO_RESEND_ABORT_REASONS = frozenset({"timeout", "cancel_text"})


async def _panel_input_not_command(_filter, _client, message) -> bool:
    text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
    return not str(text).lstrip().startswith("/")


_PANEL_INPUT_FILTER = filters.create(
    _panel_input_not_command,
    name="PanelInputNotCommand",
)


@dataclass
class AskResult:
    """Result of a pyromod ask prompt."""

    message: Message | None
    abort_reason: str | None = None
    user_notified: bool = False


def _client_declares_callable(client: Client, name: str) -> bool:
    """Avoid treating an unconfigured ``MagicMock`` attribute as a real API."""
    candidate = getattr(client, name, None)
    if not callable(candidate):
        return False
    return callable(getattr(type(client), name, None)) or name in vars(client)


async def safe_delete_user_input(message: Message | None) -> bool:
    """Best-effort cleanup for a user's temporary typed input message."""
    if message is None:
        return False
    delete_fn = getattr(message, "delete", None)
    if not callable(delete_fn):
        return False
    try:
        result = delete_fn()
        if hasattr(result, "__await__"):
            await result
        return True
    except Exception as exc:
        logger.debug(
            "temporary input delete failed message_id=%s reason=%s",
            getattr(message, "id", None) or getattr(message, "message_id", None),
            type(exc).__name__,
        )
        return False


async def prompt_for_panel_input(
    client: Client,
    chat_id: int,
    user_id: int | None,
    text: str,
    reply_markup,
    *,
    timeout: int,
) -> Message:
    """Edit the active panel into a prompt, then wait without sending a new prompt.

    Pyromod ``Client.ask`` always owns prompt delivery.  ``Client.listen`` lets
    the bot keep one existing panel message while still using the same listener
    lifecycle.  The ``ask`` fallback is retained only for environments where
    ``listen`` is genuinely unavailable (notably lightweight unit-test stubs).
    """
    if _client_declares_callable(client, "listen"):
        await deliver_ask_outcome(
            client,
            chat_id,
            user_id,
            text,
            reply_markup,
        )
        listen_kwargs = {
            "chat_id": chat_id,
            "timeout": timeout,
            "filters": _PANEL_INPUT_FILTER,
        }
        if user_id is not None:
            listen_kwargs["user_id"] = user_id
        return await client.listen(**listen_kwargs)

    return await client.ask(
        chat_id,
        text,
        timeout=timeout,
        reply_markup=reply_markup,
    )


async def safe_stop_chat_callback_listeners(client: Client, chat_id: int) -> bool:
    """Clear all pyromod CALLBACK_QUERY listeners scoped to a chat.

    Chat-wide listeners (no ``message_id``) block unrelated inline-keyboard
    handlers via pyromod's ``unallowed_click_alert`` path.
    """
    stop_fn = getattr(client, "stop_listening", None)
    if not callable(stop_fn):
        return False

    try:
        from pyromod.types import ListenerTypes
    except ImportError:
        return False

    try:
        await stop_fn(
            listener_type=ListenerTypes.CALLBACK_QUERY,
            chat_id=chat_id,
        )
        logger.debug("cleared chat callback listeners for chat_id=%s", chat_id)
        return True
    except TypeError:
        try:
            await stop_fn(ListenerTypes.CALLBACK_QUERY, chat_id)
            return True
        except Exception as exc:
            logger.debug(
                "stop_listening chat legacy failed chat_id=%s: %s",
                chat_id,
                type(exc).__name__,
            )
            return False
    except Exception as exc:
        logger.debug(
            "stop_listening chat failed chat_id=%s: %s",
            chat_id,
            type(exc).__name__,
        )
        return False


async def safe_stop_message_callback_listeners(
    client: Client,
    chat_id: int,
    message_id: int,
) -> bool:
    """Clear every pyromod CALLBACK_QUERY listener bound to one message.

    A stale listener registered for another user on the same inline-keyboard
    message makes pyromod's ``unallowed_click_alert`` path return ``False``
    before the handler regex is evaluated, which blocks unrelated routers such
    as Help Center ``h:*:U{id}`` callbacks.
    """
    stop_fn = getattr(client, "stop_listening", None)
    if not callable(stop_fn):
        logger.debug(
            "stop_listening unavailable for message listeners chat_id=%s message_id=%s",
            chat_id,
            message_id,
        )
        return False

    try:
        from pyromod.types import ListenerTypes
    except ImportError:
        logger.debug("pyromod ListenerTypes unavailable; skip message listener clear")
        return False

    try:
        await stop_fn(
            listener_type=ListenerTypes.CALLBACK_QUERY,
            chat_id=chat_id,
            message_id=message_id,
        )
        logger.debug(
            "cleared callback listeners for chat_id=%s message_id=%s",
            chat_id,
            message_id,
        )
        return True
    except TypeError:
        try:
            await stop_fn(ListenerTypes.CALLBACK_QUERY, chat_id, message_id)
            return True
        except Exception as exc:
            logger.debug(
                "stop_listening legacy signature failed chat_id=%s message_id=%s: %s",
                chat_id,
                message_id,
                type(exc).__name__,
            )
            return False
    except Exception as exc:
        logger.debug(
            "stop_listening failed for chat_id=%s message_id=%s: %s",
            chat_id,
            message_id,
            type(exc).__name__,
        )
        return False


async def safe_stop_listening(
    client: Client,
    chat_id: int,
    *,
    user_id: int | None = None,
) -> bool:
    """Clear any stale pyromod listener for a chat without failing the caller.

    Tries keyword, positional, and alternate pyromod/kurigram signatures.
    Returns True when a signature completed without raising.
    """
    stop_fn = getattr(client, "stop_listening", None)
    if not callable(stop_fn):
        logger.debug("stop_listening unavailable on client for chat_id=%s", chat_id)
        return False

    attempts: list[tuple[tuple, dict]] = []
    if user_id is not None:
        attempts.extend(
            [
                ((), {"chat_id": chat_id, "user_id": user_id}),
                ((chat_id, user_id), {}),
                ((), {"user_id": user_id}),
            ]
        )
    attempts.extend(
        [
            ((), {"chat_id": chat_id}),
            ((chat_id,), {}),
        ]
    )

    last_exc: Exception | None = None
    for args, kwargs in attempts:
        try:
            await stop_fn(*args, **kwargs)
            logger.debug(
                "stop_listening ok for chat_id=%s user_id=%s via args=%s kwargs=%s",
                chat_id,
                user_id,
                args,
                list(kwargs.keys()),
            )
            return True
        except TypeError as exc:
            last_exc = exc
            continue
        except Exception as exc:
            logger.debug(
                "stop_listening failed for chat_id=%s user_id=%s: %s",
                chat_id,
                user_id,
                type(exc).__name__,
            )
            continue

    if last_exc is not None:
        logger.debug(
            "stop_listening exhausted signatures for chat_id=%s user_id=%s: %s",
            chat_id,
            user_id,
            type(last_exc).__name__,
        )
    return False


async def notify_ask_abort(
    client: Client,
    chat_id: int,
    result: AskResult,
    *,
    return_to: str,
    lang: str = "fa",
    user_id: int | None = None,
) -> bool:
    """Return True when a handler should stop after an aborted ask.

    Sends user-visible feedback only for ``listener_stopped`` and unknown abort
    reasons. Timeout and cancel paths already replied inside ``_ask()``.
    """
    if result.message is not None:
        return False

    abort_reason = result.abort_reason
    if not result.user_notified and abort_reason not in _NO_RESEND_ABORT_REASONS:
        from app.services.panel_message_service import deliver_panel_outcome
        from app.services.wizard_ui import build_done_kb

        await deliver_panel_outcome(
            client,
            chat_id,
            user_id,
            t(lang, _ABORT_MESSAGE_KEY),
            build_done_kb(lang, return_to),
        )
    return True


async def deliver_ask_outcome(
    client: Client,
    chat_id: int,
    user_id: int | None,
    text: str,
    reply_markup,
    *,
    query_message=None,
) -> None:
    """Edit-first panel outcome after ask flows (success, timeout, validation)."""
    from app.services.panel_message_service import deliver_panel_outcome

    await deliver_panel_outcome(
        client,
        chat_id,
        user_id,
        text,
        reply_markup,
        query_message=query_message,
    )


async def safe_stop_propagation(update) -> bool:
    """Best-effort stop_propagation that supports sync and async implementations."""
    stop_fn = getattr(update, "stop_propagation", None)
    if not callable(stop_fn):
        return False
    try:
        result = stop_fn()
        if hasattr(result, "__await__"):
            await result
        return True
    except Exception:
        logger.debug(
            "stop_propagation failed for update type=%s", type(update).__name__
        )
        return False
