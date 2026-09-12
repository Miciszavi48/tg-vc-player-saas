"""In-bot OTP wizard for adding helper accounts with anti-ban fingerprinting.

FSM steps (every step has Back + Cancel):
  1. Ask phone number
  2. send_code → ask OTP (spaced/dashed to prevent Telegram revocation)
  3. sign_in → if SessionPasswordNeeded → ask 2FA password
  4. Export session, encrypt, bind fingerprint, save to DB
  5. Display success summary with device info
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from loguru import logger

from app.utils.diagnostic_logging import log_handler_phase, mask_phone, safe_exc_name
from pyrogram import Client, filters
from pyrogram.errors import PasswordHashInvalid, SessionPasswordNeeded
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.config.settings import instance_session_name, settings
from app.database.engine import async_session
from app.database.models import HelperAccount
from app.handlers.priority import HELPER_OTP_INPUT_GROUP, PANEL_CALLBACK_GROUP
from app.repositories import helper_event_repo
from app.services import helper_app_identity_service
from app.services.helper_app_identity_service import HelperAppIdentityError
from app.services import helper_otp_pre_auth_registry
from app.services.helper_pool_service import HelperPoolService
from app.utils.cache import acquire_lock, get_redis, release_lock
from app.utils.decorators import developer_only
from app.utils.filters import dev_filter, private_chat_filter
from app.utils.helpers import ensure_bounded_int
from app.utils.i18n import AUTO_LANG, label, t
from app.utils.redis_keys import (
    TTL_HELPER_OTP,
    helper_add_lock_key,
    helper_otp_state_key,
    helper_proxy_state_key,
    wizard_return_key,
)
from app.utils.ask_result import safe_stop_listening, safe_stop_propagation
from app.utils.ui import CB, compatible_inline_button
from app.services.panel_message_service import deliver_panel_outcome
from app.services.wizard_ui import (
    TOKEN_HELPER_HOME,
    WIZARD_HELPER_OTP,
    build_done_kb,
    detect_active_wizard_state_in_redis,
    remember_return_token,
    safe_edit_navigation_message,
)

_LANG = AUTO_LANG
_pm_dev = dev_filter() & private_chat_filter()
_PHONE_RE = re.compile(r"^\+?\d{10,15}$")
_CODE_RE = re.compile(r"\d")
_IMPORT_BACK_SESSION = "hlp:imp:back:session"
_IMPORT_BACK_PHONE = "hlp:imp:back:phone"
_IMPORT_BACK_MAX_CALLS = "hlp:imp:back:max_calls"

_PRE_AUTH_SESSION_STATE_KEY = "pre_auth_session_enc"
_PENDING_SESSION_STATE_KEY = "pending_session_enc"
_IMPORT_SESSION_STATE_KEY = "import_session_enc"
_OTP_CODE_MIN_LEN = 4
_OTP_CODE_MAX_LEN = 8
_SAFE_HELPER_OTP_TEXT_FIELDS = frozenset({
    "active_wizard",
    "command",
    "reason",
    "state_step",
})
_DIGIT_TRANSLATION = str.maketrans(
    {
        "۰": "0",
        "۱": "1",
        "۲": "2",
        "۳": "3",
        "۴": "4",
        "۵": "5",
        "۶": "6",
        "۷": "7",
        "۸": "8",
        "۹": "9",
        "٠": "0",
        "١": "1",
        "٢": "2",
        "٣": "3",
        "٤": "4",
        "٥": "5",
        "٦": "6",
        "٧": "7",
        "٨": "8",
        "٩": "9",
    }
)


@dataclass(frozen=True)
class _AuthFailure:
    key: str
    clear_state: bool
    done_keyboard: bool
    kwargs: dict | None = None


class _RegistryStoreError(Exception):
    """Pre-auth client registry store failed after send_code."""


class _StateStoreError(Exception):
    """Redis wizard state persist failed after send_code."""


class _PhoneCodeHashMissingError(Exception):
    """send_code returned no phone_code_hash."""


@dataclass
class _SendCodeCtx:
    """Mutable context for phased send_code diagnostics."""

    user_id: int
    phone: str
    phase: str = "phone_input_received"
    client_connected: bool = False
    code_likely_sent: bool = False
    has_phone_code_hash: bool = False
    session_export_attempted: bool = False
    credential_id: int | None = None
    device_profile_id: int | None = None


def _safe_bool(value: object) -> bool:
    return bool(value)


def _state_log_fields(state: dict | None) -> dict:
    state = state or {}
    return {
        "state_step": state.get("step"),
        "has_phone": _safe_bool(state.get("phone")),
        "has_phone_code_hash": _safe_bool(state.get("phone_code_hash")),
        "has_pre_auth_session": _safe_bool(state.get(_PRE_AUTH_SESSION_STATE_KEY)),
        "has_pending_session": _safe_bool(state.get(_PENDING_SESSION_STATE_KEY)),
        "has_import_session": _safe_bool(state.get(_IMPORT_SESSION_STATE_KEY)),
        "credential_id": state.get(helper_app_identity_service.STATE_CREDENTIAL_ID_KEY),
        "device_profile_id": state.get(helper_app_identity_service.STATE_DEVICE_PROFILE_ID_KEY),
    }


def _log_helper_otp_event(
    event: str,
    *,
    user_id: int | None = None,
    chat_id: int | None = None,
    phone: str | None = None,
    step: str | None = None,
    result: str | None = None,
    exc: BaseException | None = None,
    level: str = "info",
    **fields,
) -> None:
    parts = [event]
    if user_id is not None:
        parts.append(f"user_id={user_id}")
    if chat_id is not None:
        parts.append(f"chat_id={chat_id}")
    if phone:
        parts.append(f"phone={mask_phone(phone)}")
    if step:
        parts.append(f"step={step}")
    if result:
        parts.append(f"result={result}")
    if exc is not None:
        parts.append(f"exception_class={safe_exc_name(exc)}")
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, bool):
            parts.append(f"{key}={value}")
            continue
        if isinstance(value, int):
            parts.append(f"{key}={value}")
            continue
        key_lower = key.lower()
        if any(
            marker in key_lower
            for marker in ("raw", "password", "session_string", "api_hash", "token", "secret", "payload")
        ):
            continue
        if isinstance(value, str) and key in _SAFE_HELPER_OTP_TEXT_FIELDS:
            parts.append(f"{key}={value}")
    message = " ".join(parts)
    if level == "warning":
        logger.warning(message)
    elif level == "error":
        logger.error(message)
    elif level == "debug":
        logger.debug(message)
    else:
        logger.info(message)


# ── FSM state in Redis ──────────────────────────────────────────────────

async def _set_state(user_id: int, state: dict) -> None:
    try:
        r = await get_redis()
        await r.set(helper_otp_state_key(user_id), json.dumps(state), ex=TTL_HELPER_OTP)
    except Exception as exc:
        _log_helper_otp_event(
            "helper_otp.state.save.failure",
            user_id=user_id,
            step=str((state or {}).get("step") or ""),
            result="failed",
            exc=exc,
            level="warning",
            **_state_log_fields(state),
        )
        raise
    _log_helper_otp_event(
        "helper_otp.state.save.success",
        user_id=user_id,
        step=str((state or {}).get("step") or ""),
        result="success",
        level="debug",
        **_state_log_fields(state),
    )


async def _get_state(user_id: int) -> dict | None:
    r = await get_redis()
    raw = await r.get(helper_otp_state_key(user_id))
    if not isinstance(raw, (str, bytes, bytearray)):
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _new_panel_state(query: CallbackQuery, step: str) -> dict:
    message = query.message
    message_id = getattr(message, "id", None) or getattr(message, "message_id", None)
    return {
        "step": step,
        "panel_chat_id": message.chat.id,
        "panel_message_id": message_id,
    }


async def _render_input_panel(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
):
    """Redraw the remembered helper panel after a typed wizard input."""
    message_vars = getattr(message, "__dict__", {})
    client = (
        message_vars.get("_client")
        if isinstance(message_vars, dict) and "_client" in message_vars
        else None
    )
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if client is not None and isinstance(chat_id, int) and isinstance(user_id, int):
        return await deliver_panel_outcome(
            client,
            chat_id,
            user_id,
            text,
            reply_markup,
        )
    return await message.reply(text, reply_markup=reply_markup)


async def _clear_state(user_id: int) -> None:
    try:
        await helper_otp_pre_auth_registry.evict(user_id, phase="clear_state")
        r = await get_redis()
        await r.delete(
            helper_otp_state_key(user_id),
            helper_proxy_state_key(user_id),
            wizard_return_key(user_id),
        )
    except Exception as exc:
        _log_helper_otp_event(
            "helper_otp.state.cleanup.failure",
            user_id=user_id,
            result="failed",
            exc=exc,
            level="warning",
        )
        raise
    _log_helper_otp_event(
        "helper_otp.state.cleanup.success",
        user_id=user_id,
        result="success",
        level="debug",
    )

async def _safe_disconnect(
    temp_client: Client | None,
    *,
    step: str | None = None,
    user_id: int | None = None,
) -> None:
    if temp_client is None:
        return
    try:
        await temp_client.disconnect()
        _log_helper_otp_event(
            "helper_otp.client.disconnect.success",
            user_id=user_id,
            step=step,
            result="success",
            level="debug",
        )
        if step:
            logger.debug(
                "helper_otp_auth cleanup step={} user_id={} disconnected=True",
                step,
                user_id,
            )
    except Exception as exc:
        _log_helper_otp_event(
            "helper_otp.client.disconnect.failure",
            user_id=user_id,
            step=step,
            result="failed",
            exc=exc,
            level="warning",
        )
        logger.warning(
            "helper_otp_auth cleanup step={} user_id={} disconnected=False exc={}",
            step,
            user_id,
            type(exc).__name__,
        )
        return


async def _safe_delete_user_message(message: Message | None) -> None:
    """Best-effort deletion for sensitive user inputs (OTP/2FA/session)."""
    if message is None:
        return
    delete_fn = getattr(message, "delete", None)
    if not callable(delete_fn):
        return
    try:
        result = delete_fn()
        if hasattr(result, "__await__"):
            await result
    except Exception:
        return


def _build_otp_client(*, user_id: int, state: dict, name_suffix: str, session_string: str | None = None) -> Client:
    kwargs: dict = {
        "name": instance_session_name(f"otp_{name_suffix}_{user_id}"),
        "api_id": helper_app_identity_service.api_id_from_state(state),
        "api_hash": helper_app_identity_service.api_hash_from_state(state),
        "in_memory": True,
        "device_model": state.get("fp_device") or "",
        "system_version": state.get("fp_system") or "",
        "app_version": state.get("fp_app") or "",
        "lang_code": state.get("fp_lang") or "en",
        "system_lang_code": state.get("fp_system_lang") or state.get("fp_lang") or "en-US",
    }
    if session_string is not None:
        kwargs["session_string"] = session_string
    return Client(**kwargs)


async def _persist_session_state(state: dict, temp_client: Client, key: str) -> None:
    session_string = await temp_client.export_session_string()
    state[key] = HelperPoolService.encrypt_session(session_string)


async def _persist_pre_auth_session(state: dict, temp_client: Client) -> None:
    """Persist the send_code auth context for the later sign_in step."""
    await _persist_session_state(state, temp_client, _PRE_AUTH_SESSION_STATE_KEY)


async def _persist_pending_session(state: dict, temp_client: Client) -> None:
    """Persist a resumable auth context after SessionPasswordNeeded.

    Stores the exported session string encrypted using the helper-session Fernet key,
    so Redis never holds plaintext session data.
    """
    await _persist_session_state(state, temp_client, _PENDING_SESSION_STATE_KEY)


def _try_load_pending_session_plaintext(state: dict) -> str | None:
    return _try_load_session_plaintext(state, _PENDING_SESSION_STATE_KEY)


def _try_load_pre_auth_session_plaintext(state: dict) -> str | None:
    return _try_load_session_plaintext(state, _PRE_AUTH_SESSION_STATE_KEY)


def _try_load_session_plaintext(state: dict, key: str) -> str | None:
    enc = state.get(key)
    if not enc:
        return None
    try:
        return HelperPoolService.decrypt_session(str(enc))
    except Exception:
        return None


def _normalize_import_phone(phone: str) -> str:
    """Normalize phone numbers for import/session comparison."""
    normalized = _normalize_phone_input(phone)
    if not normalized:
        return ""
    if not normalized.startswith("+"):
        normalized = f"+{normalized}"
    return normalized


def _try_load_import_session_plaintext(state: dict) -> str | None:
    enc = state.get(_IMPORT_SESSION_STATE_KEY)
    if not enc:
        return None
    try:
        return HelperPoolService.decrypt_session(str(enc))
    except Exception:
        return None


def _looks_like_phone(text: str | None) -> bool:
    """Return True when message text resembles a phone number."""
    normalized = _normalize_phone_input(text or "")
    return bool(_PHONE_RE.match(normalized))


def _normalize_digits(text: str | None) -> str:
    return (text or "").translate(_DIGIT_TRANSLATION)


def _normalize_phone_input(text: str | None) -> str:
    return _normalize_digits(text).strip().replace(" ", "").replace("-", "")


def _normalize_otp_code(text: str | None) -> str:
    normalized = _normalize_digits(text)
    normalized = re.sub(r"[\s\-]+", "", normalized.strip())
    if not normalized.isdigit():
        return ""
    if len(normalized) < _OTP_CODE_MIN_LEN or len(normalized) > _OTP_CODE_MAX_LEN:
        return ""
    return normalized


def _flood_wait_seconds(exc: Exception) -> int:
    raw = getattr(exc, "value", None)
    if raw is None:
        raw = getattr(exc, "x", None)
    try:
        wait = int(raw)
    except Exception:
        wait = 60
    return max(wait, 1)


def _is_network_error(exc: Exception) -> bool:
    name = type(exc).__name__
    return isinstance(exc, (ConnectionError, OSError, TimeoutError)) or any(
        marker in name
        for marker in (
            "Connection",
            "Network",
            "Proxy",
            "Socket",
            "Timeout",
        )
    )


def _classify_auth_failure(exc: Exception, *, step: str) -> _AuthFailure:
    name = type(exc).__name__
    if isinstance(exc, HelperAppIdentityError):
        if exc.reason == "missing_app_credentials":
            return _AuthFailure("admin.helpers.otp_fail_app_credentials_missing", True, True)
        if exc.reason == "invalid_api_id":
            return _AuthFailure("admin.helpers.otp_fail_app_api_id", True, True)
        if exc.reason in {"invalid_api_hash", "api_hash_decrypt_failed", "api_hash_encrypt_failed"}:
            return _AuthFailure("admin.helpers.otp_fail_app_api_hash", True, True)
        return _AuthFailure("admin.helpers.otp_fail_app_auth", True, True)
    if "PhoneNumberInvalid" in name:
        return _AuthFailure("admin.helpers.otp_fail_phone", True, True)
    if "PhoneNumberBanned" in name:
        return _AuthFailure("admin.helpers.otp_fail_phone_banned", True, True)
    if "PhoneNumberFlood" in name or "FloodWait" in name:
        return _AuthFailure(
            "admin.helpers.otp_fail_flood",
            True,
            True,
            {"wait": _flood_wait_seconds(exc)},
        )
    if "PhoneCodeInvalid" in name or "PhoneCodeEmpty" in name:
        return _AuthFailure("admin.helpers.otp_fail_code", False, False)
    if "PhoneCodeExpired" in name:
        return _AuthFailure("admin.helpers.otp_fail_code_expired", True, True)
    if "PhoneCodeHashEmpty" in name or "CodeHashInvalid" in name:
        return _AuthFailure("admin.helpers.otp_fail_state_expired", True, True)
    if "PasswordHashInvalid" in name:
        return _AuthFailure("admin.helpers.otp_fail_2fa", False, False)
    if (
        "AuthKeyUnregistered" in name
        or "AuthKeyDuplicated" in name
        or "AuthKeyInvalid" in name
        or "AuthKeyPermEmpty" in name
        or "SessionRevoked" in name
        or "SessionExpired" in name
    ):
        return _AuthFailure("admin.helpers.otp_fail_auth_session", True, True)
    if "ApiIdInvalid" in name or "ConnectionApiIdInvalid" in name or "ApiIdPublishedFlood" in name:
        return _AuthFailure("admin.helpers.otp_fail_app_auth", True, True)
    if _is_network_error(exc):
        return _AuthFailure("admin.helpers.otp_fail_network", True, True)
    return _AuthFailure("admin.helpers.otp_fail_unknown", True, True)


def _classify_send_code_failure(
    exc: Exception,
    *,
    phase: str,
    code_likely_sent: bool,
) -> _AuthFailure:
    if isinstance(exc, _StateStoreError):
        return _AuthFailure("admin.helpers.otp_state_store_failed", True, True)
    if isinstance(exc, (_RegistryStoreError, _PhoneCodeHashMissingError)):
        return _AuthFailure("admin.helpers.otp_send_code_post_failure", True, True)
    if code_likely_sent and phase not in {
        "phone_input_received",
        "identity_selected",
        "client_created",
        "client_connected",
        "send_code_started",
    }:
        return _AuthFailure("admin.helpers.otp_send_code_post_failure", True, True)
    return _classify_auth_failure(exc, step=phase)


def _log_auth_event(
    *,
    step: str,
    user_id: int,
    phone: str | None = None,
    exc: Exception | None = None,
    has_phone_code_hash: bool | None = None,
    has_pre_auth_session: bool | None = None,
    session_saved: bool | None = None,
    credential_id: int | None = None,
    device_profile_id: int | None = None,
    client_connected: bool | None = None,
    code_likely_sent: bool | None = None,
    session_export_attempted: bool | None = None,
    level: str = "debug",
) -> None:
    exc_name = type(exc).__name__ if exc else None
    wait = _flood_wait_seconds(exc) if exc and exc_name and "FloodWait" in exc_name else None
    masked = mask_phone(phone or "") if phone else None
    message = (
        "helper_otp_auth step={} user_id={} phone={} exc={} flood_wait={} "
        "phone_code_hash={} pre_auth_session={} session_saved={} credential_id={} "
        "device_profile_id={} client_connected={} code_likely_sent={} "
        "session_export_attempted={}"
    ).format(
        step,
        user_id,
        masked,
        exc_name,
        wait,
        has_phone_code_hash,
        has_pre_auth_session,
        session_saved,
        credential_id,
        device_profile_id,
        client_connected,
        code_likely_sent,
        session_export_attempted,
    )
    if level == "warning":
        logger.warning(message)
    elif level == "info":
        logger.info(message)
    else:
        logger.debug(message)


def _log_phase_failure(ctx: _SendCodeCtx, exc: Exception) -> None:
    _log_auth_event(
        step=f"{ctx.phase}_failed",
        user_id=ctx.user_id,
        phone=ctx.phone,
        exc=exc,
        has_phone_code_hash=ctx.has_phone_code_hash,
        has_pre_auth_session=helper_otp_pre_auth_registry.has(ctx.user_id),
        session_saved=False,
        credential_id=ctx.credential_id,
        device_profile_id=ctx.device_profile_id,
        client_connected=ctx.client_connected,
        code_likely_sent=ctx.code_likely_sent,
        session_export_attempted=ctx.session_export_attempted,
        level="warning",
    )


async def _reply_auth_failure(
    message: Message,
    exc: Exception,
    *,
    step: str,
    phone: str | None,
    state: dict | None = None,
    retry_back_cb: str,
) -> _AuthFailure:
    failure = _classify_auth_failure(exc, step=step)
    _log_auth_event(
        step=f"{step}_failed",
        user_id=message.from_user.id,
        phone=phone,
        exc=exc,
        has_phone_code_hash=bool((state or {}).get("phone_code_hash")),
        has_pre_auth_session=helper_otp_pre_auth_registry.has(message.from_user.id),
        credential_id=(state or {}).get(helper_app_identity_service.STATE_CREDENTIAL_ID_KEY),
        device_profile_id=(state or {}).get(helper_app_identity_service.STATE_DEVICE_PROFILE_ID_KEY),
        level="warning",
    )
    reply_markup = (
        build_done_kb(_LANG, TOKEN_HELPER_HOME)
        if failure.done_keyboard
        else _nav_kb(back_cb=retry_back_cb)
    )
    await _render_input_panel(
        message,
        t(_LANG, failure.key, **(failure.kwargs or {})),
        reply_markup=reply_markup,
    )
    return failure


async def _reply_auth_context_lost(message: Message, *, step: str, state: dict | None = None) -> None:
    _log_helper_otp_event(
        "helper_otp.auth_context.lost",
        user_id=message.from_user.id,
        chat_id=message.chat.id if message.chat else None,
        phone=(state or {}).get("phone"),
        step=step,
        result="lost",
        level="warning",
        **_state_log_fields(state),
    )
    _log_auth_event(
        step=f"{step}_auth_context_lost",
        user_id=message.from_user.id,
        phone=(state or {}).get("phone"),
        has_phone_code_hash=bool((state or {}).get("phone_code_hash")),
        has_pre_auth_session=helper_otp_pre_auth_registry.has(message.from_user.id),
        credential_id=(state or {}).get(helper_app_identity_service.STATE_CREDENTIAL_ID_KEY),
        device_profile_id=(state or {}).get(helper_app_identity_service.STATE_DEVICE_PROFILE_ID_KEY),
        level="warning",
    )
    await _render_input_panel(
        message,
        t(_LANG, "admin.helpers.otp_auth_context_lost"),
        reply_markup=build_done_kb(_LANG, TOKEN_HELPER_HOME),
    )
    await _clear_state(message.from_user.id)


async def _reply_state_expired(message: Message, *, step: str, state: dict | None = None) -> None:
    _log_helper_otp_event(
        "helper_otp.state.expired",
        user_id=message.from_user.id,
        chat_id=message.chat.id if message.chat else None,
        phone=(state or {}).get("phone"),
        step=step,
        result="expired",
        level="warning",
        **_state_log_fields(state),
    )
    _log_auth_event(
        step=f"{step}_state_expired",
        user_id=message.from_user.id,
        phone=(state or {}).get("phone"),
        has_phone_code_hash=bool((state or {}).get("phone_code_hash")),
        has_pre_auth_session=bool((state or {}).get(_PRE_AUTH_SESSION_STATE_KEY)),
        credential_id=(state or {}).get(helper_app_identity_service.STATE_CREDENTIAL_ID_KEY),
        device_profile_id=(state or {}).get(helper_app_identity_service.STATE_DEVICE_PROFILE_ID_KEY),
    )
    await _render_input_panel(
        message,
        t(_LANG, "admin.helpers.otp_fail_state_expired"),
        reply_markup=build_done_kb(_LANG, TOKEN_HELPER_HOME),
    )
    await _clear_state(message.from_user.id)


# ── Keyboards ───────────────────────────────────────────────────────────

def _nav_kb(back_cb: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    if back_cb:
        rows.append(
            [InlineKeyboardButton(t(_LANG, "common.buttons.back"), callback_data=back_cb)]
        )
        rows.append(
            [
                compatible_inline_button(
                    t(_LANG, "common.buttons.cancel_inline"),
                    callback_data="hlp:otp:cancel",
                )
            ]
        )
    else:
        rows.append(
            [
                compatible_inline_button(
                    t(_LANG, "common.buttons.cancel_inline"),
                    callback_data="hlp:otp:cancel",
                ),
                compatible_inline_button(
                    t(_LANG, "common.buttons.home"),
                    callback_data=CB["WZ_HOME"],
                ),
            ]
        )
    return InlineKeyboardMarkup(rows)


def _success_kb(helper_id: int) -> InlineKeyboardMarkup:
    done_rows = build_done_kb(_LANG, TOKEN_HELPER_HOME).inline_keyboard
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t(_LANG, "admin.helpers.open_detail_btn"),
                    callback_data=f"{CB['HLP_DETAIL_PREFIX']}{helper_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    t(_LANG, "admin.helpers.set_proxy_btn"),
                    callback_data=f"{CB['HLP_SET_PROXY_PREFIX']}{helper_id}",
                )
            ],
            *done_rows,
        ]
    )


async def _edit_otp_wizard_message(
    client: Client,
    query: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> bool:
    return await safe_edit_navigation_message(
        client,
        query.message,
        text,
        reply_markup=reply_markup,
        user_id=query.from_user.id,
    )


# ── Handler registration ────────────────────────────────────────────────

def register(bot: Client, call_py) -> None:  # noqa: ARG001

    # ── Entry point ─────────────────────────────────────────────────

    @bot.on_callback_query(
        filters.regex(f"^{CB['HLP_ADD_OTP']}$") & _pm_dev,
        group=PANEL_CALLBACK_GROUP,
    )
    @developer_only
    async def otp_start(client: Client, query: CallbackQuery):
        _log_helper_otp_event(
            "helper_otp.start.clicked",
            user_id=query.from_user.id if query.from_user else None,
            chat_id=query.message.chat.id if query.message and query.message.chat else None,
            result="received",
        )
        await query.answer()
        if query.message and query.message.chat:
            _log_helper_otp_event(
                "helper_otp.start.interruption_cleanup.start",
                user_id=query.from_user.id,
                chat_id=query.message.chat.id,
                result="start",
            )
            await safe_stop_listening(client, query.message.chat.id, user_id=query.from_user.id)
            _log_helper_otp_event(
                "helper_otp.start.interruption_cleanup.success",
                user_id=query.from_user.id,
                chat_id=query.message.chat.id,
                result="success",
            )
        logger.debug("OTP start clicked user_id={}", query.from_user.id)
        await _clear_state(query.from_user.id)
        await remember_return_token(query.from_user.id, TOKEN_HELPER_HOME)
        await _set_state(query.from_user.id, _new_panel_state(query, "awaiting_phone"))
        logger.debug("OTP state written user_id={} step=awaiting_phone", query.from_user.id)
        ok = await _edit_otp_wizard_message(
            client,
            query,
            t(_LANG, "admin.helpers.otp_ask_phone"),
            reply_markup=_nav_kb(back_cb=CB["HLP_ADD"]),
        )
        _log_helper_otp_event(
            "helper_otp.phone_prompt.sent",
            user_id=query.from_user.id,
            chat_id=query.message.chat.id if query.message and query.message.chat else None,
            result="success" if ok else "failed",
            level="info" if ok else "warning",
        )

    @bot.on_callback_query(
        filters.regex(f"^{CB['HLP_IMPORT_SESSION']}$") & _pm_dev,
        group=PANEL_CALLBACK_GROUP,
    )
    @developer_only
    async def import_start(client: Client, query: CallbackQuery):
        _log_helper_otp_event(
            "helper_import.start.clicked",
            user_id=query.from_user.id if query.from_user else None,
            chat_id=query.message.chat.id if query.message and query.message.chat else None,
            result="received",
        )
        await query.answer()
        if query.message and query.message.chat:
            await safe_stop_listening(client, query.message.chat.id, user_id=query.from_user.id)
        logger.debug("Helper import start clicked user_id={}", query.from_user.id)
        await _clear_state(query.from_user.id)
        await remember_return_token(query.from_user.id, TOKEN_HELPER_HOME)
        await _set_state(
            query.from_user.id,
            _new_panel_state(query, "awaiting_import_session"),
        )
        logger.debug("Helper import state written user_id={} step=awaiting_import_session", query.from_user.id)
        await _edit_otp_wizard_message(
            client,
            query,
            t(_LANG, "admin.helpers.import_ask_session"),
            reply_markup=_nav_kb(back_cb=CB["HLP_ADD"]),
        )

    # ── Cancel ──────────────────────────────────────────────────────

    @bot.on_callback_query(filters.regex(r"^hlp:otp:cancel$") & _pm_dev)
    @developer_only
    async def otp_cancel(client: Client, query: CallbackQuery):
        _log_helper_otp_event(
            "helper_otp.cancel.clicked",
            user_id=query.from_user.id if query.from_user else None,
            chat_id=query.message.chat.id if query.message and query.message.chat else None,
            result="received",
        )
        await _clear_state(query.from_user.id)
        ok = await _edit_otp_wizard_message(
            client,
            query,
            t(_LANG, "admin.helpers.otp_cancelled"),
            reply_markup=build_done_kb(_LANG, TOKEN_HELPER_HOME),
        )
        if ok:
            await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
            _log_helper_otp_event(
                "helper_otp.cancel.cleanup.success",
                user_id=query.from_user.id,
                chat_id=query.message.chat.id if query.message and query.message.chat else None,
                result="success",
            )
        else:
            await query.answer(t(_LANG, "common.errors.navigation_failed"), show_alert=True)
            _log_helper_otp_event(
                "helper_otp.cancel.cleanup.failure",
                user_id=query.from_user.id,
                chat_id=query.message.chat.id if query.message and query.message.chat else None,
                result="edit_failed",
                level="warning",
            )

    # ── Back to phone step ──────────────────────────────────────────

    @bot.on_callback_query(filters.regex(r"^hlp:otp:back:phone$") & _pm_dev)
    @developer_only
    async def otp_back_phone(client: Client, query: CallbackQuery):
        await query.answer()
        await _set_state(query.from_user.id, _new_panel_state(query, "awaiting_phone"))
        await _edit_otp_wizard_message(
            client,
            query,
            t(_LANG, "admin.helpers.otp_ask_phone"),
            reply_markup=_nav_kb(back_cb=CB["HLP_ADD"]),
        )

    # ── Back to code step ───────────────────────────────────────────

    @bot.on_callback_query(filters.regex(r"^hlp:otp:back:code$") & _pm_dev)
    @developer_only
    async def otp_back_code(client: Client, query: CallbackQuery):
        await query.answer()
        state = await _get_state(query.from_user.id)
        if not state or "phone" not in state:
            await _set_state(query.from_user.id, _new_panel_state(query, "awaiting_phone"))
            await _edit_otp_wizard_message(
                client,
                query,
                t(_LANG, "admin.helpers.otp_ask_phone"),
                reply_markup=_nav_kb(back_cb=CB["HLP_ADD"]),
            )
            return
        state["step"] = "awaiting_code"
        await _set_state(query.from_user.id, state)
        await _edit_otp_wizard_message(
            client,
            query,
            t(_LANG, "admin.helpers.otp_ask_code"),
            reply_markup=_nav_kb(back_cb="hlp:otp:back:phone"),
        )

    @bot.on_callback_query(filters.regex(r"^hlp:imp:back:session$") & _pm_dev)
    @developer_only
    async def import_back_session(client: Client, query: CallbackQuery):
        await query.answer()
        state = await _get_state(query.from_user.id) or {}
        state["step"] = "awaiting_import_session"
        await _set_state(query.from_user.id, state)
        await _edit_otp_wizard_message(
            client,
            query,
            t(_LANG, "admin.helpers.import_ask_session"),
            reply_markup=_nav_kb(back_cb=CB["HLP_ADD"]),
        )

    @bot.on_callback_query(filters.regex(r"^hlp:imp:back:phone$") & _pm_dev)
    @developer_only
    async def import_back_phone(client: Client, query: CallbackQuery):
        await query.answer()
        state = await _get_state(query.from_user.id)
        if not state or not state.get(_IMPORT_SESSION_STATE_KEY):
            await _set_state(
                query.from_user.id,
                _new_panel_state(query, "awaiting_import_session"),
            )
            await _edit_otp_wizard_message(
                client,
                query,
                t(_LANG, "admin.helpers.import_ask_session"),
                reply_markup=_nav_kb(back_cb=CB["HLP_ADD"]),
            )
            return
        state["step"] = "awaiting_import_phone"
        await _set_state(query.from_user.id, state)
        await _edit_otp_wizard_message(
            client,
            query,
            t(_LANG, "admin.helpers.import_ask_phone"),
            reply_markup=_nav_kb(back_cb=_IMPORT_BACK_SESSION),
        )

    @bot.on_callback_query(filters.regex(r"^hlp:imp:back:max_calls$") & _pm_dev)
    @developer_only
    async def import_back_max_calls(client: Client, query: CallbackQuery):
        await query.answer()
        state = await _get_state(query.from_user.id)
        if not state or not state.get("phone"):
            await _set_state(
                query.from_user.id,
                _new_panel_state(query, "awaiting_import_phone"),
            )
            await _edit_otp_wizard_message(
                client,
                query,
                t(_LANG, "admin.helpers.import_ask_phone"),
                reply_markup=_nav_kb(back_cb=_IMPORT_BACK_SESSION),
            )
            return
        state["step"] = "awaiting_import_max_calls"
        await _set_state(query.from_user.id, state)
        await _edit_otp_wizard_message(
            client,
            query,
            t(_LANG, "admin.helpers.import_ask_max_calls"),
            reply_markup=_nav_kb(back_cb=_IMPORT_BACK_PHONE),
        )

    # ── Text input dispatcher ───────────────────────────────────────

    @bot.on_message(
        filters.private & filters.text & dev_filter(),
        group=HELPER_OTP_INPUT_GROUP,
    )
    async def otp_text_handler(client: Client, message: Message):
        from app.services.wizard_ui import (
            clear_runtime_state,
            clear_wizard_and_allow_command,
        )

        user_id = message.from_user.id
        text = message.text or ""
        if await clear_wizard_and_allow_command(client, message, lang=_LANG):
            return
        state = await _get_state(user_id)
        phone_like = _looks_like_phone(text)
        _log_helper_otp_event(
            "helper_otp.text.received",
            user_id=user_id,
            chat_id=message.chat.id if message.chat else None,
            result="received",
            phone_like=phone_like,
        )
        logger.debug(
            "OTP text handler entered user_id=%s phone_like=%s",
            user_id,
            phone_like,
        )

        redis_client = await get_redis()
        active_wizard, _ = await detect_active_wizard_state_in_redis(
            redis_client,
            user_id,
            heal_conflicts=True,
        )
        if active_wizard and active_wizard != WIZARD_HELPER_OTP:
            if phone_like:
                _log_helper_otp_event(
                    "helper_otp.text.other_wizard_active",
                    user_id=user_id,
                    chat_id=message.chat.id if message.chat else None,
                    result="ignored",
                    active_wizard=active_wizard,
                    level="warning",
                )
                logger.debug(
                    "OTP phone ignored: other wizard active user_id=%s wizard=%s",
                    user_id,
                    active_wizard,
                )
                await _render_input_panel(
                    message,
                    t(_LANG, "admin.helpers.otp_other_wizard_active"),
                    reply_markup=build_done_kb(_LANG, TOKEN_HELPER_HOME),
                )
                await safe_stop_propagation(message)
            return

        state = await _get_state(user_id)
        if state and state.get("step") == "awaiting_phone":
            await safe_stop_listening(client, message.chat.id, user_id=user_id)

        if text.startswith("/"):
            await clear_runtime_state(client, user_id, message.chat.id)
            return

        if not state:
            return_token = await redis_client.get(wizard_return_key(user_id))
            if phone_like and return_token == TOKEN_HELPER_HOME:
                _log_helper_otp_event(
                    "helper_otp.state.expired",
                    user_id=user_id,
                    chat_id=message.chat.id if message.chat else None,
                    result="expired",
                    level="warning",
                )
                logger.debug(
                    "OTP phone ignored: session expired user_id=%s",
                    user_id,
                )
                await _safe_delete_user_message(message)
                await _render_input_panel(
                    message,
                    t(_LANG, "admin.helpers.otp_session_expired"),
                    reply_markup=build_done_kb(_LANG, TOKEN_HELPER_HOME),
                )
                await redis_client.delete(wizard_return_key(user_id))
                await safe_stop_propagation(message)
            return
        await _safe_delete_user_message(message)
        if text.startswith("/cancel"):
            _log_helper_otp_event(
                "helper_otp.cancel.command",
                user_id=message.from_user.id,
                chat_id=message.chat.id if message.chat else None,
                step=str(state.get("step") or ""),
                result="cancelled",
            )
            await _clear_state(message.from_user.id)
            await _render_input_panel(
                message,
                t(_LANG, "admin.helpers.otp_cancelled"),
                reply_markup=build_done_kb(_LANG, TOKEN_HELPER_HOME),
            )
            await safe_stop_propagation(message)
            return

        step = state.get("step")
        _log_helper_otp_event(
            "helper_otp.state.active",
            user_id=user_id,
            chat_id=message.chat.id if message.chat else None,
            step=str(step or ""),
            result="dispatch",
            **_state_log_fields(state),
        )
        logger.debug("OTP text handler active state user_id={} step={}", user_id, step)
        handled = True
        if step == "awaiting_phone":
            await _handle_phone(client, message, state)
        elif step == "awaiting_code":
            await _handle_code(client, message, state)
        elif step == "awaiting_2fa":
            await _handle_2fa(client, message, state)
        elif step == "awaiting_import_session":
            await _handle_import_session(message, state)
        elif step == "awaiting_import_phone":
            await _handle_import_phone(message, state)
        elif step == "awaiting_import_max_calls":
            await _handle_import_max_calls(message, state)
        elif step == "awaiting_import_max_joins":
            await _handle_import_max_joins(client, message, state)
        else:
            handled = False
        if handled:
            await safe_stop_propagation(message)

    @bot.on_message(
        filters.private & filters.contact & dev_filter(),
        group=HELPER_OTP_INPUT_GROUP,
    )
    async def otp_contact_handler(client: Client, message: Message):  # noqa: ARG001
        user_id = message.from_user.id
        redis_client = await get_redis()
        active_wizard, _ = await detect_active_wizard_state_in_redis(
            redis_client,
            user_id,
            heal_conflicts=True,
        )
        if active_wizard and active_wizard != WIZARD_HELPER_OTP:
            return

        state = await _get_state(user_id)
        if not state or state.get("step") != "awaiting_phone":
            return

        await _safe_delete_user_message(message)
        await _render_input_panel(
            message,
            t(_LANG, "admin.helpers.otp_phone_as_text"),
            reply_markup=_nav_kb(back_cb=CB["HLP_ADD"]),
        )
        await safe_stop_propagation(message)


# ── Step handlers ────────────────────────────────────────────────────────

async def _handle_phone(client: Client, message: Message, state: dict) -> None:
    user_id = message.from_user.id
    phone = _normalize_phone_input(message.text)
    ctx = _SendCodeCtx(user_id=user_id, phone=phone, phase="phone_input_received")
    _log_helper_otp_event(
        "helper_otp.phone.input.received",
        user_id=user_id,
        chat_id=message.chat.id if message.chat else None,
        phone=phone,
        result="received",
    )
    if not _PHONE_RE.match(phone):
        _log_helper_otp_event(
            "helper_otp.phone.validation.failed",
            user_id=user_id,
            chat_id=message.chat.id if message.chat else None,
            phone=phone,
            result="invalid_format",
            level="warning",
        )
        await _render_input_panel(
            message,
            t(_LANG, "admin.helpers.otp_fail_phone"),
            reply_markup=_nav_kb(back_cb=CB["HLP_ADD"]),
        )
        return
    _log_helper_otp_event(
        "helper_otp.phone.validation.passed",
        user_id=user_id,
        chat_id=message.chat.id if message.chat else None,
        phone=phone,
        result="valid",
    )

    async with async_session() as session:
        existing = await session.execute(
            select(HelperAccount).where(HelperAccount.phone == phone))
        if existing.scalar_one_or_none() is not None:
            _log_helper_otp_event(
                "helper_otp.phone.validation.failed",
                user_id=user_id,
                chat_id=message.chat.id if message.chat else None,
                phone=phone,
                result="duplicate",
                level="warning",
            )
            await _render_input_panel(
                message,
                t(_LANG, "admin.helpers.otp_duplicate"),
                reply_markup=_nav_kb(back_cb=CB["HLP_ADD"]),
            )
            return

    logger.info(
        "helper_otp phase={} user_id={} phone={}",
        ctx.phase,
        user_id,
        mask_phone(phone),
    )
    await _render_input_panel(message, t(_LANG, "admin.helpers.otp_sending_code"))

    temp_client: Client | None = None
    registry_owned = False
    try:
        ctx.phase = "identity_selected"
        _log_helper_otp_event(
            "helper_otp.identity.select.start",
            user_id=user_id,
            phone=phone,
            result="start",
        )
        identity = await helper_app_identity_service.select_identity()
        identity_state = helper_app_identity_service.state_fields_for_identity(identity)
        ctx.credential_id = identity.credential_id
        ctx.device_profile_id = identity.device_profile_id
        _log_helper_otp_event(
            "helper_otp.identity.select.success",
            user_id=user_id,
            phone=phone,
            result="success",
            credential_id=identity.credential_id,
            device_profile_id=identity.device_profile_id,
        )

        ctx.phase = "client_created"
        temp_client = _build_otp_client(
            user_id=user_id,
            state=identity_state,
            name_suffix="send",
        )
        _log_helper_otp_event(
            "helper_otp.pre_auth_client.created",
            user_id=user_id,
            phone=phone,
            result="created",
            credential_id=identity.credential_id,
            device_profile_id=identity.device_profile_id,
        )

        ctx.phase = "client_connected"
        await temp_client.connect()
        ctx.client_connected = True
        _log_helper_otp_event(
            "helper_otp.pre_auth_client.connected",
            user_id=user_id,
            phone=phone,
            result="success",
        )

        ctx.phase = "send_code_started"
        _log_helper_otp_event(
            "helper_otp.send_code.start",
            user_id=user_id,
            phone=phone,
            result="start",
        )
        sent_code = await temp_client.send_code(phone)

        ctx.phase = "send_code_completed"
        ctx.code_likely_sent = True
        _log_helper_otp_event(
            "helper_otp.send_code.success",
            user_id=user_id,
            phone=phone,
            result="success",
            credential_id=identity.credential_id,
            device_profile_id=identity.device_profile_id,
        )
        logger.info(
            "helper_otp phase=send_code_completed user_id={} phone={} credential_id={} device_profile_id={}",
            user_id,
            mask_phone(phone),
            identity.credential_id,
            identity.device_profile_id,
        )

        ctx.phase = "phone_code_hash_extracted"
        phone_code_hash = str(getattr(sent_code, "phone_code_hash", "") or "").strip()
        if not phone_code_hash:
            raise _PhoneCodeHashMissingError("missing_phone_code_hash")
        ctx.has_phone_code_hash = True
        _log_helper_otp_event(
            "helper_otp.phone_code_hash.present",
            user_id=user_id,
            phone=phone,
            result="present",
        )

        state.update({
            "step": "awaiting_code",
            "phone": phone,
            "phone_code_hash": phone_code_hash,
            **identity_state,
        })

        ctx.phase = "pre_auth_registry_store_started"
        _log_helper_otp_event(
            "helper_otp.pre_auth_registry.store.start",
            user_id=user_id,
            phone=phone,
            result="start",
        )
        try:
            await helper_otp_pre_auth_registry.put(
                user_id,
                client=temp_client,
                phone=phone,
                phone_code_hash=phone_code_hash,
            )
            registry_owned = True
            temp_client = None
        except Exception as exc:
            _log_helper_otp_event(
                "helper_otp.pre_auth_registry.store.failure",
                user_id=user_id,
                phone=phone,
                result="failed",
                exc=exc,
                level="warning",
            )
            raise _RegistryStoreError("registry_put_failed") from exc

        ctx.phase = "pre_auth_registry_store_completed"
        _log_helper_otp_event(
            "helper_otp.pre_auth_registry.store.success",
            user_id=user_id,
            phone=phone,
            result="success",
            has_pre_auth_session=True,
        )

        ctx.phase = "wizard_state_store_started"
        try:
            await _set_state(user_id, state)
        except Exception as exc:
            await helper_otp_pre_auth_registry.evict(user_id, phase="wizard_state_store_failed")
            registry_owned = False
            raise _StateStoreError("redis_set_failed") from exc

        ctx.phase = "wizard_state_store_completed"

        ctx.phase = "credential_profile_usage_marked"
        try:
            await helper_app_identity_service.mark_identity_used(identity)
        except Exception as exc:
            logger.warning(
                "helper_otp phase=credential_profile_usage_marked user_id={} exc={}",
                user_id,
                type(exc).__name__,
            )

        ctx.phase = "code_prompt_sent"
        await _render_input_panel(
            message,
            t(_LANG, "admin.helpers.otp_ask_code"),
            reply_markup=_nav_kb(back_cb="hlp:otp:back:phone"),
        )
        _log_helper_otp_event(
            "helper_otp.otp_prompt.sent",
            user_id=user_id,
            phone=phone,
            result="success",
        )
        logger.info("helper_otp phase=code_prompt_sent user_id={}", user_id)
        _log_auth_event(
            step="send_code_success",
            user_id=user_id,
            phone=phone,
            has_phone_code_hash=True,
            has_pre_auth_session=True,
            session_saved=True,
            credential_id=identity.credential_id,
            device_profile_id=identity.device_profile_id,
            client_connected=True,
            code_likely_sent=True,
            session_export_attempted=False,
            level="info",
        )
    except Exception as exc:
        failure = _classify_send_code_failure(
            exc,
            phase=ctx.phase,
            code_likely_sent=ctx.code_likely_sent,
        )
        _log_phase_failure(ctx, exc)
        _log_helper_otp_event(
            "helper_otp.send_code.failure",
            user_id=user_id,
            phone=phone,
            step=ctx.phase,
            result="failed",
            exc=exc,
            has_phone_code_hash=ctx.has_phone_code_hash,
            has_pre_auth_session=helper_otp_pre_auth_registry.has(user_id),
            credential_id=ctx.credential_id,
            device_profile_id=ctx.device_profile_id,
            client_connected=ctx.client_connected,
            code_likely_sent=ctx.code_likely_sent,
            level="warning",
        )
        reply_markup = (
            build_done_kb(_LANG, TOKEN_HELPER_HOME)
            if failure.done_keyboard
            else _nav_kb(back_cb=CB["HLP_ADD"])
        )
        await _render_input_panel(
            message,
            t(_LANG, failure.key, **(failure.kwargs or {})),
            reply_markup=reply_markup,
        )
        if registry_owned:
            await helper_otp_pre_auth_registry.evict(user_id, phase="send_code_failure")
        else:
            ctx.phase = "client_disconnected"
            await _safe_disconnect(temp_client, step="send_code", user_id=user_id)
        await _clear_state(user_id)


async def _handle_code(client: Client, message: Message, state: dict) -> None:
    user_id = message.from_user.id
    raw_code_len = len(message.text or "")
    _log_helper_otp_event(
        "helper_otp.otp.input.received",
        user_id=user_id,
        chat_id=message.chat.id if message.chat else None,
        result="received",
        otp_length=raw_code_len,
    )
    digits = _normalize_otp_code(message.text)
    await _safe_delete_user_message(message)
    _log_helper_otp_event(
        "helper_otp.otp.normalized",
        user_id=user_id,
        chat_id=message.chat.id if message.chat else None,
        result="valid" if bool(digits) else "invalid",
        otp_length=len(digits),
        format_valid=bool(digits),
    )
    if not digits:
        await _render_input_panel(
            message,
            t(_LANG, "admin.helpers.otp_fail_code_format"),
            reply_markup=_nav_kb(back_cb="hlp:otp:back:phone"),
        )
        return

    await _render_input_panel(
        message,
        t(_LANG, "admin.helpers.otp_code_received"),
        reply_markup=_nav_kb(back_cb="hlp:otp:back:phone"),
    )
    phone = str(state.get("phone") or "")
    phone_code_hash = str(state.get("phone_code_hash") or "")
    entry = helper_otp_pre_auth_registry.get(user_id)
    resumed_from_pre_auth = False
    if entry is None:
        pre_auth_session = _try_load_pre_auth_session_plaintext(state)
        if pre_auth_session:
            temp_client = _build_otp_client(
                user_id=user_id,
                state=state,
                name_suffix="signin",
                session_string=pre_auth_session,
            )
            try:
                await temp_client.connect()
                resumed_from_pre_auth = True
            except Exception as exc:
                _log_helper_otp_event(
                    "helper_otp.sign_in.failure",
                    user_id=user_id,
                    phone=phone,
                    result="pre_auth_connect_failed",
                    exc=exc,
                    has_phone_code_hash=bool(phone_code_hash),
                    has_pre_auth_session=True,
                    level="warning",
                )
                await _reply_auth_context_lost(message, step="sign_in", state=state)
                return
        else:
            temp_client = None
    else:
        temp_client = entry.client

    if not phone or not phone_code_hash or temp_client is None:
        _log_helper_otp_event(
            "helper_otp.sign_in.failure",
            user_id=user_id,
            phone=phone,
            result="missing_auth_context",
            has_phone_code_hash=bool(phone_code_hash),
            has_pre_auth_session=entry is not None,
            level="warning",
        )
        await _reply_auth_context_lost(message, step="sign_in", state=state)
        return

    try:
        try:
            _log_helper_otp_event(
                "helper_otp.sign_in.start",
                user_id=user_id,
                phone=phone,
                result="start",
                has_phone_code_hash=bool(phone_code_hash),
                has_pre_auth_session=True,
            )
            await temp_client.sign_in(phone, phone_code_hash, digits)
        except SessionPasswordNeeded:
            state["step"] = "awaiting_2fa"
            await _persist_pending_session(state, temp_client)
            await _set_state(user_id, state)
            await _safe_disconnect(temp_client, step="sign_in_2fa_required", user_id=user_id)
            await helper_otp_pre_auth_registry.evict(user_id, phase="2fa_required_pending_session")
            _log_helper_otp_event(
                "helper_otp.sign_in.2fa_required",
                user_id=user_id,
                phone=phone,
                result="2fa_required",
                has_phone_code_hash=bool(phone_code_hash),
                has_pre_auth_session=True,
                credential_id=state.get(helper_app_identity_service.STATE_CREDENTIAL_ID_KEY),
                device_profile_id=state.get(helper_app_identity_service.STATE_DEVICE_PROFILE_ID_KEY),
            )
            _log_auth_event(
                step="sign_in_2fa_required",
                user_id=user_id,
                phone=phone,
                has_phone_code_hash=bool(phone_code_hash),
                has_pre_auth_session=True,
                session_saved=True,
                credential_id=state.get(helper_app_identity_service.STATE_CREDENTIAL_ID_KEY),
                device_profile_id=state.get(helper_app_identity_service.STATE_DEVICE_PROFILE_ID_KEY),
                level="info",
            )
            await _render_input_panel(
                message,
                t(_LANG, "admin.helpers.otp_ask_2fa"),
                reply_markup=_nav_kb(back_cb="hlp:otp:back:code"),
            )
            _log_helper_otp_event(
                "helper_otp.2fa_prompt.sent",
                user_id=user_id,
                phone=phone,
                result="success",
            )
            return
        except Exception as exc:
            _log_helper_otp_event(
                "helper_otp.sign_in.failure",
                user_id=user_id,
                phone=phone,
                result="failed",
                exc=exc,
                has_phone_code_hash=bool(phone_code_hash),
                has_pre_auth_session=True,
                credential_id=state.get(helper_app_identity_service.STATE_CREDENTIAL_ID_KEY),
                device_profile_id=state.get(helper_app_identity_service.STATE_DEVICE_PROFILE_ID_KEY),
                level="warning",
            )
            failure = await _reply_auth_failure(
                message,
                exc,
                step="sign_in",
                phone=phone,
                state=state,
                retry_back_cb="hlp:otp:back:phone",
            )
            if failure.clear_state:
                await _clear_state(user_id)
            if resumed_from_pre_auth:
                await _safe_disconnect(temp_client, step="sign_in_failure", user_id=user_id)
            return

        _log_helper_otp_event(
            "helper_otp.sign_in.success",
            user_id=user_id,
            phone=phone,
            result="success",
        )
        await _finalize_helper(client, message, temp_client, state)
    except Exception as exc:
        _log_helper_otp_event(
            "helper_otp.sign_in.failure",
            user_id=user_id,
            phone=phone,
            result="unhandled",
            exc=exc,
            level="warning",
        )
        failure = await _reply_auth_failure(
            message,
            exc,
            step="sign_in_unhandled",
            phone=phone,
            state=state,
            retry_back_cb="hlp:otp:back:phone",
        )
        if failure.clear_state:
            await _clear_state(user_id)


async def _handle_2fa(client: Client, message: Message, state: dict) -> None:
    user_id = message.from_user.id
    password = message.text.strip()
    _log_helper_otp_event(
        "helper_otp.2fa_password.received",
        user_id=user_id,
        chat_id=message.chat.id if message.chat else None,
        phone=state.get("phone"),
        result="received",
    )
    await _safe_delete_user_message(message)

    entry = helper_otp_pre_auth_registry.get(user_id)
    resumed_from_pending = False
    if entry is None:
        pending_session = _try_load_pending_session_plaintext(state)
        if pending_session:
            temp_client = _build_otp_client(
                user_id=user_id,
                state=state,
                name_suffix="2fa",
                session_string=pending_session,
            )
            try:
                await temp_client.connect()
                resumed_from_pending = True
            except Exception as exc:
                _log_helper_otp_event(
                    "helper_otp.check_password.failure",
                    user_id=user_id,
                    phone=state.get("phone"),
                    result="pending_session_connect_failed",
                    exc=exc,
                    level="warning",
                )
                await _reply_auth_context_lost(message, step="check_password", state=state)
                return
        else:
            temp_client = None
    else:
        temp_client = entry.client

    if temp_client is None:
        _log_helper_otp_event(
            "helper_otp.check_password.failure",
            user_id=user_id,
            phone=state.get("phone"),
            result="missing_auth_context",
            level="warning",
        )
        await _reply_auth_context_lost(message, step="check_password", state=state)
        return

    await _render_input_panel(
        message,
        t(_LANG, "admin.helpers.otp_2fa_received"),
        reply_markup=_nav_kb(back_cb="hlp:otp:back:code"),
    )
    try:
        try:
            _log_helper_otp_event(
                "helper_otp.check_password.start",
                user_id=user_id,
                phone=state.get("phone"),
                result="start",
                has_pre_auth_session=True,
            )
            await temp_client.check_password(password)
        except PasswordHashInvalid as exc:
            _log_helper_otp_event(
                "helper_otp.check_password.failure",
                user_id=user_id,
                phone=state.get("phone"),
                result="invalid_password",
                exc=exc,
                has_pre_auth_session=True,
                level="warning",
            )
            await _reply_auth_failure(
                message,
                exc,
                step="check_password",
                phone=state.get("phone"),
                state=state,
                retry_back_cb="hlp:otp:back:code",
            )
            if resumed_from_pending:
                await _safe_disconnect(temp_client, step="check_password_invalid", user_id=user_id)
            return
        except Exception as exc:
            _log_helper_otp_event(
                "helper_otp.check_password.failure",
                user_id=user_id,
                phone=state.get("phone"),
                result="failed",
                exc=exc,
                has_pre_auth_session=True,
                level="warning",
            )
            failure = await _reply_auth_failure(
                message,
                exc,
                step="check_password",
                phone=state.get("phone"),
                state=state,
                retry_back_cb="hlp:otp:back:code",
            )
            if failure.clear_state:
                await _clear_state(user_id)
            if resumed_from_pending:
                await _safe_disconnect(temp_client, step="check_password_failure", user_id=user_id)
            return

        _log_helper_otp_event(
            "helper_otp.check_password.success",
            user_id=user_id,
            phone=state.get("phone"),
            result="success",
        )
        await _finalize_helper(client, message, temp_client, state)
    except Exception as exc:
        _log_helper_otp_event(
            "helper_otp.check_password.failure",
            user_id=user_id,
            phone=state.get("phone"),
            result="unhandled",
            exc=exc,
            level="warning",
        )
        failure = await _reply_auth_failure(
            message,
            exc,
            step="check_password_unhandled",
            phone=state.get("phone"),
            state=state,
            retry_back_cb="hlp:otp:back:code",
        )
        if failure.clear_state:
            await _clear_state(user_id)
        if resumed_from_pending:
            await _safe_disconnect(temp_client, step="check_password_unhandled", user_id=user_id)


async def _handle_import_session(message: Message, state: dict) -> None:
    session_string = (message.text or "").strip()
    await _safe_delete_user_message(message)
    if len(session_string) < 20:
        await _render_input_panel(
            message,
            t(_LANG, "admin.helpers.import_fail_session"),
            reply_markup=_nav_kb(back_cb=CB["HLP_ADD"]),
        )
        return

    try:
        state[_IMPORT_SESSION_STATE_KEY] = HelperPoolService.encrypt_session(session_string)
    except Exception:
        await _render_input_panel(
            message,
            t(_LANG, "admin.helpers.import_fail_session"),
            reply_markup=build_done_kb(_LANG, TOKEN_HELPER_HOME),
        )
        await _clear_state(message.from_user.id)
        return
    state["step"] = "awaiting_import_phone"
    await _set_state(message.from_user.id, state)
    await _render_input_panel(
        message,
        t(_LANG, "admin.helpers.import_ask_phone"),
        reply_markup=_nav_kb(back_cb=_IMPORT_BACK_SESSION),
    )


async def _handle_import_phone(message: Message, state: dict) -> None:
    phone = (message.text or "").strip().replace(" ", "").replace("-", "")
    if not _PHONE_RE.match(phone):
        await _render_input_panel(
            message,
            t(_LANG, "admin.helpers.otp_fail_phone"),
            reply_markup=_nav_kb(back_cb=_IMPORT_BACK_SESSION),
        )
        return

    state["phone"] = phone
    state["step"] = "awaiting_import_max_calls"
    await _set_state(message.from_user.id, state)
    await _render_input_panel(
        message,
        t(_LANG, "admin.helpers.import_ask_max_calls"),
        reply_markup=_nav_kb(back_cb=_IMPORT_BACK_PHONE),
    )


def _parse_positive_int(raw: str, *, min_value: int, max_value: int) -> int | None:
    try:
        value = int(raw.strip())
    except Exception:
        return None
    if value < min_value or value > max_value:
        return None
    return value


async def _handle_import_max_calls(message: Message, state: dict) -> None:
    value = _parse_positive_int(message.text or "", min_value=1, max_value=500)
    if value is None:
        await _render_input_panel(
            message,
            t(_LANG, "common.errors.invalid_number"),
            reply_markup=_nav_kb(back_cb=_IMPORT_BACK_PHONE),
        )
        return

    state["max_concurrent_calls"] = value
    state["step"] = "awaiting_import_max_joins"
    await _set_state(message.from_user.id, state)
    await _render_input_panel(
        message,
        t(_LANG, "admin.helpers.import_ask_max_joins"),
        reply_markup=_nav_kb(back_cb=_IMPORT_BACK_MAX_CALLS),
    )


async def _handle_import_max_joins(client: Client, message: Message, state: dict) -> None:
    value = _parse_positive_int(message.text or "", min_value=1, max_value=10000)
    if value is None:
        await _render_input_panel(
            message,
            t(_LANG, "common.errors.invalid_number"),
            reply_markup=_nav_kb(back_cb=_IMPORT_BACK_MAX_CALLS),
        )
        return

    state["max_joins_per_hour"] = value
    await _set_state(message.from_user.id, state)
    await _render_input_panel(
        message,
        t(_LANG, "admin.helpers.import_verifying"),
        reply_markup=_nav_kb(back_cb=_IMPORT_BACK_MAX_CALLS),
    )
    await _finalize_import_helper(client, message, state)


async def _verify_import_session(
    user_id: int,
    session_string: str,
) -> tuple[int, str | None, str | None, str | None]:
    """Verify an imported session and return account identity fields."""
    temp_client = Client(
        name=instance_session_name(f"imp_{user_id}"),
        api_id=settings.API_ID,
        api_hash=settings.API_HASH,
        session_string=session_string,
        in_memory=True,
    )
    async with temp_client:
        me = await temp_client.get_me()
    session_phone = getattr(me, "phone_number", None) or None
    if isinstance(session_phone, str) and not session_phone.strip():
        session_phone = None
    return me.id, me.username, me.first_name, session_phone


async def _finalize_import_helper(
    _bot_client: Client,
    message: Message,
    state: dict,
) -> None:
    phone = str(state.get("phone") or "").strip()
    session_string = _try_load_import_session_plaintext(state)
    if state.get("import_session") and not state.get(_IMPORT_SESSION_STATE_KEY):
        session_string = None
    if not phone or not session_string:
        await _render_input_panel(
            message,
            t(_LANG, "admin.helpers.import_fail_session"),
            reply_markup=build_done_kb(_LANG, TOKEN_HELPER_HOME),
        )
        await _clear_state(message.from_user.id)
        return

    lock_key = helper_add_lock_key(phone)
    lock_token = await acquire_lock(lock_key, ttl_ms=60_000)
    if lock_token is None:
        await _render_input_panel(
            message,
            t(_LANG, "admin.helpers.otp_conflict_retry"),
            reply_markup=build_done_kb(_LANG, TOKEN_HELPER_HOME),
        )
        await _clear_state(message.from_user.id)
        return

    try:
        tg_user_id, username, first_name, session_phone = await _verify_import_session(
            message.from_user.id,
            session_string,
        )
    except Exception as exc:
        await _render_input_panel(
            message,
            t(_LANG, "admin.helpers.import_fail_session"),
            reply_markup=build_done_kb(_LANG, TOKEN_HELPER_HOME),
        )
        await helper_event_repo.log_event(
            "helper.import_failed",
            actor="import_wizard",
            metadata={"reason": type(exc).__name__},
        )
        await release_lock(lock_key, lock_token)
        await _clear_state(message.from_user.id)
        return

    entered_phone = _normalize_import_phone(phone)
    session_phone = _normalize_import_phone(session_phone or "")
    if session_phone and entered_phone != session_phone:
        await _render_input_panel(
            message,
            t(_LANG, "admin.helpers.import_phone_mismatch"),
            reply_markup=build_done_kb(_LANG, TOKEN_HELPER_HOME),
        )
        await helper_event_repo.log_event(
            "helper.import_failed",
            actor="import_wizard",
            metadata={"reason": "phone_mismatch"},
        )
        await release_lock(lock_key, lock_token)
        await _clear_state(message.from_user.id)
        return

    try:
        max_calls = ensure_bounded_int(
            int(state.get("max_concurrent_calls") or settings.HELPER_DEFAULT_MAX_CALLS),
            field_name="max_calls",
            min_value=1,
            max_value=500,
        )
        max_joins = ensure_bounded_int(
            int(state.get("max_joins_per_hour") or settings.HELPER_DEFAULT_MAX_JOINS_PER_HOUR),
            field_name="max_joins",
            min_value=1,
            max_value=10_000,
        )
    except (TypeError, ValueError):
        await _render_input_panel(
            message,
            t(_LANG, "common.errors.invalid_positive_number"),
            reply_markup=build_done_kb(_LANG, TOKEN_HELPER_HOME),
        )
        await release_lock(lock_key, lock_token)
        await _clear_state(message.from_user.id)
        return
    enc_session = HelperPoolService.encrypt_session(session_string)
    session_fp = HelperPoolService.fingerprint_session(session_string)

    try:
        async with async_session() as session:
            async with session.begin():
                duplicate_identity = await session.execute(
                    select(HelperAccount).where(
                        or_(
                            HelperAccount.phone == phone,
                            HelperAccount.tg_user_id == tg_user_id,
                        )
                    )
                )
                if duplicate_identity.scalar_one_or_none() is not None:
                    await _render_input_panel(
                        message,
                        t(_LANG, "admin.helpers.otp_duplicate"),
                        reply_markup=build_done_kb(_LANG, TOKEN_HELPER_HOME),
                    )
                    return

                if await HelperPoolService.is_duplicate_session(session, session_string):
                    await _render_input_panel(
                        message,
                        t(_LANG, "admin.helpers.import_duplicate_session"),
                        reply_markup=build_done_kb(_LANG, TOKEN_HELPER_HOME),
                    )
                    return

                helper = HelperAccount(
                    phone=phone,
                    session_string_enc=enc_session,
                    session_fingerprint=session_fp,
                    status="active",
                    tg_user_id=tg_user_id,
                    username=username,
                    display_name=first_name,
                    max_concurrent_calls=max_calls,
                    max_joins_per_hour=max_joins,
                )
                session.add(helper)
                await session.flush()
                hid = helper.id

        await helper_event_repo.log_event(
            "helper.import_session",
            actor="import_wizard",
            helper_account_id=hid,
            metadata={"tg_id": tg_user_id},
        )

        summary = t(
            _LANG,
            "admin.helpers.import_success_summary",
            id=hid,
            name=first_name or t(_LANG, "common.labels.na"),
            username=username or t(_LANG, "common.labels.na"),
            tg_id=tg_user_id,
            status=label(_LANG, "helper_status", "active"),
            max_calls=max_calls,
            max_joins=max_joins,
        )
        await _render_input_panel(message, summary, reply_markup=_success_kb(hid))
    except IntegrityError:
        await _render_input_panel(
            message,
            t(_LANG, "admin.helpers.import_duplicate_session"),
            reply_markup=build_done_kb(_LANG, TOKEN_HELPER_HOME),
        )
        await helper_event_repo.log_event(
            "helper.import_failed",
            actor="import_wizard",
            metadata={"reason": "duplicate_integrity"},
        )
    except Exception as exc:
        log_handler_phase(
            logger,
            handler="helper_otp_wizard",
            phase="import_finalize_failed",
            user_id=message.from_user.id if message.from_user else None,
            error=exc,
            level="warning",
        )
        await _render_input_panel(
            message,
            t(_LANG, "admin.helpers.otp_fail_generic", error=label(_LANG, "error_code", "unexpected_error")),
            reply_markup=build_done_kb(_LANG, TOKEN_HELPER_HOME),
        )
    finally:
        await release_lock(lock_key, lock_token)
        await _clear_state(message.from_user.id)


async def _finalize_helper(
    bot_client: Client,
    message: Message,
    temp_client: Client,
    state: dict,
) -> None:
    """Extract session, encrypt, bind fingerprint, save to DB."""
    phone = str(state.get("phone") or "")
    lock_key = helper_add_lock_key(phone)
    lock_token = await acquire_lock(lock_key, ttl_ms=60_000)
    if lock_token is None:
        _log_helper_otp_event(
            "helper_otp.finalize.lock.busy",
            user_id=message.from_user.id,
            phone=phone,
            result="busy",
            level="warning",
        )
        await _render_input_panel(
            message,
            t(_LANG, "admin.helpers.otp_conflict_retry"),
            reply_markup=build_done_kb(_LANG, TOKEN_HELPER_HOME),
        )
        await _clear_state(message.from_user.id)
        return

    session_exported = False
    helper_saved = False
    try:
        _log_helper_otp_event(
            "helper_otp.finalize.session_export.start",
            user_id=message.from_user.id,
            phone=phone,
            result="start",
        )
        me = await temp_client.get_me()
        session_string = await temp_client.export_session_string()
        session_exported = True
        _log_helper_otp_event(
            "helper_otp.finalize.session_export.success",
            user_id=message.from_user.id,
            phone=phone,
            result="success",
        )

        _log_helper_otp_event(
            "helper_otp.finalize.session_save.start",
            user_id=message.from_user.id,
            phone=phone,
            result="start",
        )
        enc_session = HelperPoolService.encrypt_session(session_string)
        session_fp = HelperPoolService.fingerprint_session(session_string)
        _log_helper_otp_event(
            "helper_otp.finalize.session_save.success",
            user_id=message.from_user.id,
            phone=phone,
            result="success",
        )

        async with async_session() as session:
            async with session.begin():
                _log_helper_otp_event(
                    "helper_otp.finalize.helper_db_save.start",
                    user_id=message.from_user.id,
                    phone=phone,
                    result="start",
                )
                duplicate_identity = await session.execute(
                    select(HelperAccount).where(
                        or_(
                            HelperAccount.phone == phone,
                            HelperAccount.tg_user_id == me.id,
                        )
                    )
                )
                if duplicate_identity.scalar_one_or_none() is not None:
                    _log_helper_otp_event(
                        "helper_otp.finalize.helper_db_save.failure",
                        user_id=message.from_user.id,
                        phone=phone,
                        result="duplicate_identity",
                        level="warning",
                    )
                    await _render_input_panel(
                        message,
                        t(_LANG, "admin.helpers.otp_duplicate"),
                        reply_markup=build_done_kb(_LANG, TOKEN_HELPER_HOME),
                    )
                    return

                if await HelperPoolService.is_duplicate_session(session, session_string):
                    _log_helper_otp_event(
                        "helper_otp.finalize.helper_db_save.failure",
                        user_id=message.from_user.id,
                        phone=phone,
                        result="duplicate_session",
                        level="warning",
                    )
                    await _render_input_panel(
                        message,
                        t(_LANG, "admin.helpers.otp_duplicate"),
                        reply_markup=build_done_kb(_LANG, TOKEN_HELPER_HOME),
                    )
                    return

                helper = HelperAccount(
                    phone=phone,
                    session_string_enc=enc_session,
                    session_fingerprint=session_fp,
                    status="active",
                    tg_user_id=me.id,
                    username=me.username,
                    display_name=me.first_name,
                    max_concurrent_calls=settings.HELPER_DEFAULT_MAX_CALLS,
                    max_joins_per_hour=settings.HELPER_DEFAULT_MAX_JOINS_PER_HOUR,
                    device_model=state.get("fp_device"),
                    system_version=state.get("fp_system"),
                    app_version=state.get("fp_app"),
                    lang_code=state.get("fp_lang"),
                )
                session.add(helper)
                await session.flush()
                hid = helper.id
                helper_saved = True
                _log_helper_otp_event(
                    "helper_otp.finalize.helper_db_save.success",
                    user_id=message.from_user.id,
                    phone=phone,
                    result="success",
                    helper_id=hid,
                )

        await helper_event_repo.log_event(
            "helper.add", actor="otp_wizard",
            helper_account_id=hid,
            metadata={
                "tg_id": me.id,
                "device": state.get("fp_device"),
                "credential_id": state.get(helper_app_identity_service.STATE_CREDENTIAL_ID_KEY),
                "device_profile_id": state.get(helper_app_identity_service.STATE_DEVICE_PROFILE_ID_KEY),
            },
        )
        _log_auth_event(
            step="finalize_success",
            user_id=message.from_user.id,
            phone=phone,
            has_phone_code_hash=bool(state.get("phone_code_hash")),
            has_pre_auth_session=helper_otp_pre_auth_registry.has(message.from_user.id),
            session_saved=helper_saved,
            credential_id=state.get(helper_app_identity_service.STATE_CREDENTIAL_ID_KEY),
            device_profile_id=state.get(helper_app_identity_service.STATE_DEVICE_PROFILE_ID_KEY),
            level="info",
        )

        summary = t(_LANG, "admin.helpers.otp_success_summary",
                    id=hid, name=me.first_name or t(_LANG, "common.labels.na"),
                    username=me.username or t(_LANG, "common.labels.na"), tg_id=me.id,
                    status=label(_LANG, "helper_status", "active"),
                    max_calls=settings.HELPER_DEFAULT_MAX_CALLS,
                    max_joins=settings.HELPER_DEFAULT_MAX_JOINS_PER_HOUR,
                    device=state.get("fp_device", "—"),
                    system=state.get("fp_system", "—"),
                    app_ver=state.get("fp_app", "—"),
                    lang_code=state.get("fp_lang", "—"))
        await _render_input_panel(message, summary, reply_markup=_success_kb(hid))
    except IntegrityError:
        _log_helper_otp_event(
            "helper_otp.finalize.helper_db_save.failure",
            user_id=message.from_user.id,
            phone=phone,
            result="integrity_error",
            level="warning",
        )
        await _render_input_panel(
            message,
            t(_LANG, "admin.helpers.otp_duplicate"),
            reply_markup=build_done_kb(_LANG, TOKEN_HELPER_HOME),
        )
    except Exception as exc:
        if not session_exported:
            _log_helper_otp_event(
                "helper_otp.finalize.session_export.failure",
                user_id=message.from_user.id,
                phone=phone,
                result="failed",
                exc=exc,
                level="warning",
            )
        elif not helper_saved:
            _log_helper_otp_event(
                "helper_otp.finalize.helper_db_save.failure",
                user_id=message.from_user.id,
                phone=phone,
                result="failed",
                exc=exc,
                level="warning",
            )
        _log_auth_event(
            step="finalize_failed",
            user_id=message.from_user.id,
            phone=phone,
            exc=exc,
            has_phone_code_hash=bool(state.get("phone_code_hash")),
            has_pre_auth_session=helper_otp_pre_auth_registry.has(message.from_user.id),
            session_saved=session_exported and helper_saved,
            credential_id=state.get(helper_app_identity_service.STATE_CREDENTIAL_ID_KEY),
            device_profile_id=state.get(helper_app_identity_service.STATE_DEVICE_PROFILE_ID_KEY),
            level="warning",
        )
        await _render_input_panel(
            message,
            t(_LANG, "admin.helpers.otp_fail_unknown"),
            reply_markup=build_done_kb(_LANG, TOKEN_HELPER_HOME),
        )
    finally:
        await helper_otp_pre_auth_registry.evict(message.from_user.id, phase="finalize")
        _log_helper_otp_event(
            "helper_otp.pre_auth_registry.cleanup.success",
            user_id=message.from_user.id,
            phone=phone,
            result="success",
            level="debug",
        )
        await release_lock(lock_key, lock_token)
        _log_helper_otp_event(
            "helper_otp.finalize.lock.release.success",
            user_id=message.from_user.id,
            phone=phone,
            result="success",
            level="debug",
        )
        await _clear_state(message.from_user.id)
