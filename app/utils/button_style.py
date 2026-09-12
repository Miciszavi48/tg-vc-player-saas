"""Central native Telegram semantic-style policy for inline keyboards."""

from __future__ import annotations

import functools
import inspect
import logging
import re
from contextvars import ContextVar, Token
from enum import Enum
from typing import Any

from pyrogram.enums import ButtonStyle
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

STYLE_SIMPLE = "simple"
STYLE_ADVANCED = "advanced"

_CURRENT_OWNER_SCOPE: ContextVar[int | None] = ContextVar(
    "button_style_owner_scope",
    default=None,
)
_EXPLICIT_STYLE_OVERRIDE = "_musicbot_explicit_style_override"
_SEMANTIC_INTENT = "_musicbot_semantic_intent"
_TOGGLE_CURRENTLY_ENABLED = "_musicbot_toggle_currently_enabled"

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class SemanticIntent(str, Enum):
    """Action meaning used by the centralized native style policy."""

    NEUTRAL = "neutral"
    PRIMARY = "primary"
    SUCCESS = "success"
    DANGER = "danger"


_STYLE_BY_INTENT = {
    SemanticIntent.NEUTRAL: ButtonStyle.DEFAULT,
    SemanticIntent.PRIMARY: ButtonStyle.PRIMARY,
    SemanticIntent.SUCCESS: ButtonStyle.SUCCESS,
    SemanticIntent.DANGER: ButtonStyle.DANGER,
}

_DESTRUCTIVE_TOKENS = frozenset(
    {
        "abort",
        "ban",
        "banall",
        "blacklist",
        "cancel",
        "clean",
        "cleanup",
        "cd",
        "clear",
        "clr",
        "close",
        "dec",
        "decrease",
        "deduct",
        "del",
        "delete",
        "disable",
        "dis",
        "drop",
        "exit",
        "leave",
        "lv",
        "pause",
        "q",
        "quarantine",
        "reject",
        "reload",
        "remove",
        "rm",
        "stop",
    }
)
_DESTRUCTIVE_ABORT_TOKENS = frozenset({"abort", "back", "cancel", "no", "n"})
_SUCCESS_TOKENS = frozenset(
    {
        "add",
        "apply",
        "charge",
        "ci",
        "confirm",
        "continue",
        "create",
        "en",
        "enable",
        "inc",
        "increase",
        "refresh",
        "resume",
        "retry",
        "save",
        "send",
        "submit",
        "topup",
        "unquarantine",
        "uq",
        "verify",
        "yes",
        "y",
    }
)
_NAVIGATION_TOKENS = frozenset({"back", "main", "next", "prev", "previous"})
_CLOSE_TOKENS = frozenset({"close", "exit"})
_TOGGLE_TOKENS = frozenset({"toggle"})
_NEUTRAL_CALLBACKS = frozenset(
    {
        "an:home",
        "bcw:mode:send",
        "dev:banall:home",
        "hlp:home",
        "own:banall",
    }
)


def native_button_style_supported() -> bool:
    """Return whether the imported stack exposes Kurigram native styles."""

    try:
        signature = inspect.signature(InlineKeyboardButton)
    except (TypeError, ValueError):
        return False
    return "style" in signature.parameters and hasattr(ButtonStyle, "PRIMARY")


def compatible_inline_button(
    label: str,
    *,
    style: ButtonStyle = ButtonStyle.DEFAULT,
    **kwargs: Any,
) -> InlineKeyboardButton:
    """Construct an inline button with a stock/older-Pyrogram fallback."""

    try:
        return InlineKeyboardButton(label, style=style, **kwargs)
    except TypeError:
        return InlineKeyboardButton(label, **kwargs)


def mark_explicit_style_override(
    button: InlineKeyboardButton,
) -> InlineKeyboardButton:
    """Preserve a deliberate per-button style in advanced mode.

    This is reserved for the custom eight-slot Start Menu ``keycolor`` policy.
    """

    try:
        setattr(button, _EXPLICIT_STYLE_OVERRIDE, True)
    except Exception:
        pass
    return button


def mark_semantic_intent(
    button: InlineKeyboardButton,
    intent: SemanticIntent,
) -> InlineKeyboardButton:
    """Attach an explicit action meaning without assigning a native color."""

    try:
        setattr(button, _SEMANTIC_INTENT, intent.value)
    except Exception:
        pass
    return button


def mark_toggle_state(
    button: InlineKeyboardButton,
    currently_enabled: bool,
) -> InlineKeyboardButton:
    """Record toggle state so policy colors the action that will occur next."""

    try:
        setattr(button, _TOGGLE_CURRENTLY_ENABLED, bool(currently_enabled))
    except Exception:
        pass
    return button


def _callback_data(button: InlineKeyboardButton) -> str:
    value = getattr(button, "callback_data", None)
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return ""
    return str(value or "")


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(_TOKEN_RE.findall(value.casefold()))


@functools.lru_cache(maxsize=None)
def _localized_button_labels(*keys: str) -> set[str]:
    try:
        from app.utils.i18n import texts

        return {texts.t(lang, key).strip() for lang in ("fa", "en") for key in keys}
    except Exception:
        return set()


def _is_exact_label(text: str, *keys: str) -> bool:
    return text.strip() in _localized_button_labels(*keys)


def _is_url_button(button: InlineKeyboardButton) -> bool:
    return any(
        getattr(button, field, None) is not None
        for field in (
            "url",
            "login_url",
            "web_app",
            "user_id",
            "switch_inline_query",
            "switch_inline_query_current_chat",
            "callback_game",
            "copy_text",
            "pay",
        )
    )


def _is_destructive_abort(data: str, parts: tuple[str, ...], text: str) -> bool:
    if ":sp:n:" in data:
        return True
    if "abort" in parts:
        return True
    destructive_domain = bool(set(parts) & (_DESTRUCTIVE_TOKENS - {"abort", "cancel"}))
    cancel_operation = "cancel" in parts and any(
        token in parts for token in ("abort", "no", "n")
    )
    if destructive_domain and any(
        token in parts for token in _DESTRUCTIVE_ABORT_TOKENS
    ):
        return True
    if cancel_operation:
        return True
    if destructive_domain and _is_exact_label(
        text,
        "common.buttons.back",
        "common.buttons.cancel",
        "common.buttons.cancel_inline",
    ):
        return True
    return data.endswith((":no", ":n")) and destructive_domain


def _is_navigation(data: str, parts: tuple[str, ...], text: str) -> bool:
    if text.strip() in {"‹", "›", "«", "»", "←", "→"}:
        return True
    if _is_exact_label(
        text,
        "common.buttons.back",
        "common.buttons.home",
        "common.buttons.prev",
        "common.buttons.next",
        "admin_titles.back_to_settings",
        "broadcast.wizard.back_btn",
        "help.btn.back",
        "reports.back_to_list",
        "reports.nav_back",
        "reports.pagination_next",
        "reports.pagination_prev",
        "sudo_permissions.back_to_detail",
        "sudo_permissions.back_to_list",
    ):
        return True
    if set(parts) & _NAVIGATION_TOKENS:
        return True
    return (
        data == "wz:home"
        or data == "nav:start"
        or data.startswith("wz:back:")
    )


def _is_close(parts: tuple[str, ...], text: str) -> bool:
    if set(parts) & _CLOSE_TOKENS:
        return True
    return _is_exact_label(text, "common.buttons.close", "help.btn.close")


def _is_cancel(parts: tuple[str, ...], text: str) -> bool:
    if "cancel" in parts:
        return True
    return _is_exact_label(
        text,
        "common.buttons.cancel",
        "common.buttons.cancel_inline",
        "broadcast.wizard.cancel_btn",
    )


def _is_toggle(parts: tuple[str, ...], data: str) -> bool:
    if set(parts) & _TOGGLE_TOKENS:
        return True
    return any(
        marker in data
        for marker in (
            ":auto_leave",
            ":bot_enabled",
            ":channel_security",
            ":force_join",
            ":media:audio",
            ":media:buttons",
            ":media:download",
            ":media:file",
            ":media:video",
            ":repeat",
            ":sp:t:",
            ":sudo_panel_enabled",
            ":trial",
            "Access:Music",
            "Access:Video",
            "bcw:tgt:",
            "callsec:t:",
            "fm:toggle",
            "grp:set:",
        )
    )


def _toggle_intent_from_label(text: str) -> SemanticIntent | None:
    if "✅" in text:
        return SemanticIntent.DANGER
    if "☑️" in text or "❌" in text:
        return SemanticIntent.SUCCESS
    return None


def _is_destructive(data: str, parts: tuple[str, ...]) -> bool:
    if "unquarantine" in parts or "uq" in parts or "enable" in parts:
        return False
    if ":sp:y:" in data:
        return True
    if data.startswith(("hlp:q:", "hlp:dis:")):
        return True
    return bool(set(parts) & _DESTRUCTIVE_TOKENS)


def _is_success(parts: tuple[str, ...], text: str) -> bool:
    if set(parts) & _SUCCESS_TOKENS:
        return True
    return _is_exact_label(
        text,
        "common.buttons.confirm",
        "common.buttons.resume",
        "broadcast.wizard.confirm_btn",
        "fm.check_button",
        "youtube_sessions.refresh",
    )


def infer_semantic_intent(button: InlineKeyboardButton) -> SemanticIntent:
    """Infer action meaning without changing callback or layout semantics."""

    explicit = getattr(button, _SEMANTIC_INTENT, None)
    if explicit is not None:
        try:
            return SemanticIntent(explicit)
        except ValueError:
            pass

    if _is_url_button(button):
        return SemanticIntent.NEUTRAL

    data = _callback_data(button)
    parts = _tokens(data)
    text = str(getattr(button, "text", "") or "")

    if "noop" in parts:
        return SemanticIntent.NEUTRAL

    toggle_state = getattr(button, _TOGGLE_CURRENTLY_ENABLED, None)
    if isinstance(toggle_state, bool):
        return SemanticIntent.DANGER if toggle_state else SemanticIntent.SUCCESS

    if _is_destructive_abort(data, parts, text):
        return SemanticIntent.PRIMARY
    if _is_navigation(data, parts, text):
        return SemanticIntent.PRIMARY
    if data in _NEUTRAL_CALLBACKS:
        return SemanticIntent.NEUTRAL
    if data.startswith("dl:fmt:x:"):
        return SemanticIntent.DANGER
    if _is_close(parts, text):
        return SemanticIntent.DANGER
    if _is_destructive(data, parts):
        return SemanticIntent.DANGER
    if _is_cancel(parts, text):
        return SemanticIntent.DANGER
    if _is_success(parts, text):
        return SemanticIntent.SUCCESS
    if _is_toggle(parts, data):
        return _toggle_intent_from_label(text) or SemanticIntent.NEUTRAL
    return SemanticIntent.NEUTRAL


async def _resolve_effective_mode(owner_user_id: int | None) -> str:
    try:
        from app.services import start_customization_service as start_custom

        effective = await start_custom.get_effective_style_mode(
            owner_user_id=owner_user_id,
        )
        if effective.mode == STYLE_ADVANCED:
            return STYLE_ADVANCED
    except Exception:
        logger.warning(
            "button style mode resolution failed owner_user_id=%s; using simple",
            owner_user_id,
            exc_info=True,
        )
    return STYLE_SIMPLE


def _set_button_style(button: InlineKeyboardButton, style: ButtonStyle) -> None:
    try:
        button.style = style
    except Exception:
        pass


async def apply_button_style_policy(
    reply_markup: InlineKeyboardMarkup | None,
    *,
    style_mode: str | None = None,
    owner_user_id: int | None = None,
) -> InlineKeyboardMarkup | None:
    """Apply the effective semantic policy to actual button objects."""

    if reply_markup is None or not isinstance(reply_markup, InlineKeyboardMarkup):
        return reply_markup

    mode = (
        style_mode
        if style_mode in {STYLE_SIMPLE, STYLE_ADVANCED}
        else await _resolve_effective_mode(
            _CURRENT_OWNER_SCOPE.get() if owner_user_id is None else owner_user_id
        )
    )
    advanced = mode == STYLE_ADVANCED and native_button_style_supported()

    for row in getattr(reply_markup, "inline_keyboard", ()) or ():
        for button in row:
            if not advanced:
                _set_button_style(button, ButtonStyle.DEFAULT)
                continue
            if getattr(button, _EXPLICIT_STYLE_OVERRIDE, False):
                continue
            intent = infer_semantic_intent(button)
            _set_button_style(button, _STYLE_BY_INTENT[intent])
    return reply_markup


async def _resolve_owner_scope_from_update(update: Any) -> int | None:
    cached = getattr(update, "_musicbot_button_style_owner_scope", ...)
    if cached is not ...:
        return cached if isinstance(cached, int) else None

    message = getattr(update, "message", None) or update
    chat = getattr(message, "chat", None)
    chat_type = getattr(
        getattr(chat, "type", None), "value", getattr(chat, "type", None)
    )
    user_id = getattr(getattr(update, "from_user", None), "id", None)
    if user_id is None:
        user_id = getattr(getattr(message, "from_user", None), "id", None)

    owner_scope: int | None = None
    if chat_type == "private" and isinstance(user_id, int):
        try:
            from app.repositories import user_repo
            from app.utils.bot_guards import is_developer

            if not is_developer(user_id) and await user_repo.is_owner(user_id):
                owner_scope = user_id
        except Exception:
            owner_scope = None

    try:
        setattr(update, "_musicbot_button_style_owner_scope", owner_scope)
    except Exception:
        pass
    return owner_scope


async def bind_button_style_scope_from_update(update: Any) -> Token:
    """Bind global/Owner effective style scope for one handler execution."""

    owner_user_id = await _resolve_owner_scope_from_update(update)
    return _CURRENT_OWNER_SCOPE.set(owner_user_id)


def reset_button_style_scope(token: Token | None) -> None:
    if token is not None:
        _CURRENT_OWNER_SCOPE.reset(token)


def _style_serialization_error(exc: Exception) -> bool:
    if isinstance(exc, AttributeError):
        return True
    message = str(exc).casefold()
    return isinstance(exc, TypeError) and (
        "style" in message or "keyboardbuttonstyle" in message
    )


def install_inline_keyboard_style_hook() -> None:
    """Style every reachable inline keyboard at Kurigram serialization time."""

    original_write = InlineKeyboardMarkup.write
    if getattr(original_write, "_musicbot_semantic_style_hook", False):
        return

    @functools.wraps(original_write)
    async def _write_with_semantic_styles(self, client):
        await apply_button_style_policy(self)
        styled_buttons = [
            button
            for row in getattr(self, "inline_keyboard", ()) or ()
            for button in row
            if getattr(button, "style", ButtonStyle.DEFAULT) != ButtonStyle.DEFAULT
        ]
        try:
            return await original_write(self, client)
        except (TypeError, AttributeError) as exc:
            if not styled_buttons or not _style_serialization_error(exc):
                raise
            logger.warning(
                "native button style serialization unavailable; retrying uncolored",
                exc_info=True,
            )
            for button in styled_buttons:
                _set_button_style(button, ButtonStyle.DEFAULT)
            return await original_write(self, client)

    _write_with_semantic_styles._musicbot_semantic_style_hook = True
    InlineKeyboardMarkup.write = _write_with_semantic_styles
