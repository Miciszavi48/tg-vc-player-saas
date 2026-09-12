"""Private Developer/Owner panel for global encrypted YouTube cookie sessions."""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from pyromod.exceptions import ListenerStopped

from app.repositories import user_repo, youtube_session_repo
from app.services.youtube_session_service import (
    CookieValidationError,
    has_rotation_keys,
    import_cookie_session,
    rekey_session_pool,
    test_session,
)
from app.utils.ask_result import (
    deliver_ask_outcome,
    prompt_for_panel_input,
    safe_stop_listening,
)
from app.utils.decorators import owner_or_above
from app.utils.filters import owner_filter, private_chat_filter
from app.utils.i18n import t
from app.utils.ui import CB, KeyboardFactory, compatible_inline_button
from app.utils.bot_guards import is_developer
from app.utils.cache import get_redis
from app.utils.redis_keys import TTL_YOUTUBE_REKEY_CONFIRM, youtube_rekey_confirm_claim_key
from app.handlers.priority import PANEL_CALLBACK_GROUP


_LANG = "fa"
_ASK_TIMEOUT_SECONDS = 120
_DELETE_CONFIRM_TTL_SECONDS = 300
_PAGE_SIZE = 5
_pm_owner = owner_filter() & private_chat_filter()


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return t(_LANG, "youtube_sessions.value_none")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _status_label(row) -> str:
    now = datetime.now(timezone.utc)
    if row.status == "active" and row.cooldown_until and row.cooldown_until > now:
        return t(_LANG, "youtube_sessions.status_cooldown", until=_format_datetime(row.cooldown_until))
    return t(_LANG, f"youtube_sessions.status_{row.status}")


async def _can_manage(user_id: int | None) -> bool:
    return bool(user_id is not None and (is_developer(user_id) or await user_repo.is_owner(user_id)))


def _back_callback(user_id: int) -> str:
    return CB["DEV_CAT_SETTINGS"] if is_developer(user_id) else CB["NAV_BACK"]


async def _render_home(query: CallbackQuery, *, notice: str | None = None) -> None:
    user_id = query.from_user.id
    summary = await youtube_session_repo.get_summary()
    last_success = (
        f"YT-{summary.last_success_id} · {_format_datetime(summary.last_success_at)}"
        if summary.last_success_id is not None
        else t(_LANG, "youtube_sessions.last_success_none")
    )
    text = "\n\n".join([
        t(_LANG, "youtube_sessions.title"),
        t(
            _LANG,
            "youtube_sessions.summary",
            active=summary.active,
            cooldown=summary.cooldown,
            inactive=summary.disabled_or_invalid,
            last_success=last_success,
        ),
    ])
    if notice:
        text = f"{notice}\n\n{text}"
    await query.message.edit_text(
        text,
        reply_markup=KeyboardFactory.youtube_sessions_home(
            _LANG,
            back_callback=_back_callback(user_id),
            rekey_available=has_rotation_keys(),
        ),
    )


async def _render_list(query: CallbackQuery, page: int) -> None:
    rows, total = await youtube_session_repo.list_sessions(page=page, page_size=_PAGE_SIZE)
    total_pages = max(1, math.ceil(total / _PAGE_SIZE))
    page = min(max(page, 0), total_pages - 1)
    if not rows and total:
        rows, total = await youtube_session_repo.list_sessions(page=page, page_size=_PAGE_SIZE)
    if not rows:
        await query.message.edit_text(
            t(_LANG, "youtube_sessions.list_empty"),
            reply_markup=KeyboardFactory.youtube_sessions_home(
                _LANG,
                back_callback=_back_callback(query.from_user.id),
                rekey_available=has_rotation_keys(),
            ),
        )
        return
    await query.message.edit_text(
        t(_LANG, "youtube_sessions.title"),
        reply_markup=KeyboardFactory.youtube_sessions_list(
            _LANG,
            [(row.id, _status_label(row)) for row in rows],
            page=page,
            total_pages=total_pages,
        ),
    )


async def _render_detail(
    query: CallbackQuery,
    session_id: int,
    page: int,
    *,
    notice: str | None = None,
) -> None:
    row = await youtube_session_repo.get_session(session_id)
    if row is None:
        await query.answer(t(_LANG, "youtube_sessions.not_found"), show_alert=True)
        return
    text = t(
        _LANG,
        "youtube_sessions.detail",
        session_id=row.id,
        status=_status_label(row),
        checked=_format_datetime(row.last_checked_at),
        selected=_format_datetime(row.last_selected_at),
        success=_format_datetime(row.last_success_at),
        expires=_format_datetime(row.cookie_expires_at),
        error=row.last_error_code or t(_LANG, "youtube_sessions.value_none"),
    )
    if notice:
        text = f"{notice}\n\n{text}"
    await query.message.edit_text(
        text,
        reply_markup=KeyboardFactory.youtube_session_detail(
            _LANG,
            session_id=row.id,
            status=row.status,
            page=page,
        ),
    )


def _parse_page(data: str, prefix: str) -> int | None:
    value = data.removeprefix(prefix)
    try:
        page = int(value)
    except ValueError:
        return None
    return page if page >= 0 else None


def _parse_session_page(data: str, prefix: str) -> tuple[int, int] | None:
    payload = data.removeprefix(prefix)
    parts = payload.split(":")
    if len(parts) != 2:
        return None
    try:
        session_id, page = (int(part) for part in parts)
    except ValueError:
        return None
    if session_id <= 0 or page < 0:
        return None
    return session_id, page


def _parse_delete_confirmation(data: str, prefix: str) -> tuple[int, int, int, int] | None:
    payload = data.removeprefix(prefix)
    parts = payload.split(":")
    if len(parts) != 4:
        return None
    try:
        session_id, page, user_id, issued_at = (int(part) for part in parts)
    except ValueError:
        return None
    if session_id <= 0 or page < 0 or user_id <= 0 or issued_at <= 0:
        return None
    return session_id, page, user_id, issued_at


def _parse_rekey_confirmation(data: str, prefix: str) -> tuple[int, int] | None:
    payload = data.removeprefix(prefix)
    parts = payload.split(":")
    if len(parts) != 2:
        return None
    try:
        user_id, issued_at = (int(part) for part in parts)
    except ValueError:
        return None
    if user_id <= 0 or issued_at <= 0:
        return None
    return user_id, issued_at


async def _claim_rekey_confirmation(user_id: int, issued_at: int) -> bool:
    if int(time.time()) - issued_at > _DELETE_CONFIRM_TTL_SECONDS:
        return False
    try:
        redis = await get_redis()
        claimed = await redis.set(
            youtube_rekey_confirm_claim_key(user_id, issued_at),
            "1",
            nx=True,
            ex=TTL_YOUTUBE_REKEY_CONFIRM,
        )
    except Exception:
        return False
    return bool(claimed)


def _upload_error_key(code: str) -> str:
    known = {
        "file_too_large",
        "invalid_encoding",
        "invalid_netscape_header",
        "invalid_cookie_row",
        "no_youtube_cookie_rows",
        "duplicate_cookie",
        "key_missing",
    }
    return f"youtube_sessions.error_{code}" if code in known else "youtube_sessions.error_generic"


def _document_bytes(value) -> bytes:
    if hasattr(value, "getvalue"):
        return bytes(value.getvalue())
    content = value.read()
    return bytes(content)


def _upload_prompt_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            compatible_inline_button(
                t(_LANG, "common.buttons.cancel_inline"),
                callback_data=CB["YT_SESSION_HOME"],
            )
        ]]
    )


def _upload_done_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                t(_LANG, "common.buttons.back"),
                callback_data=CB["YT_SESSION_HOME"],
            )
        ]]
    )


async def _delete_upload_message(message) -> bool:
    try:
        await message.delete()
        return True
    except Exception:
        return False


async def _ask_for_cookie_file(client: Client, query: CallbackQuery) -> None:
    user_id = query.from_user.id
    chat_id = query.message.chat.id
    await safe_stop_listening(client, chat_id, user_id=user_id)
    while True:
        try:
            message = await prompt_for_panel_input(
                client,
                chat_id,
                user_id,
                t(_LANG, "youtube_sessions.upload_prompt"),
                _upload_prompt_kb(),
                timeout=_ASK_TIMEOUT_SECONDS,
            )
        except ListenerStopped:
            return
        except Exception:
            await deliver_ask_outcome(
                client,
                chat_id,
                user_id,
                t(_LANG, "ask.timeout"),
                _upload_done_kb(),
                query_message=query.message,
            )
            return

        if not await _can_manage(user_id):
            await deliver_ask_outcome(
                client,
                chat_id,
                user_id,
                t(_LANG, "common.errors.no_access"),
                _upload_done_kb(),
                query_message=query.message,
            )
            return

        document = getattr(message, "document", None)
        if document is None or not str(getattr(document, "file_name", "")).lower().endswith(".txt"):
            deleted = await _delete_upload_message(message)
            text = t(_LANG, "youtube_sessions.upload_document_required")
            if not deleted:
                text += "\n\n" + t(_LANG, "youtube_sessions.upload_delete_warning")
            await deliver_ask_outcome(
                client,
                chat_id,
                user_id,
                text,
                _upload_prompt_kb(),
                query_message=query.message,
            )
            continue
        if int(getattr(document, "file_size", 0) or 0) > 512 * 1024:
            deleted = await _delete_upload_message(message)
            text = t(_LANG, "youtube_sessions.error_file_too_large")
            if not deleted:
                text += "\n\n" + t(_LANG, "youtube_sessions.upload_delete_warning")
            await deliver_ask_outcome(
                client,
                chat_id,
                user_id,
                text,
                _upload_prompt_kb(),
                query_message=query.message,
            )
            continue

        result = None
        error_key: str | None = None
        try:
            downloaded = await client.download_media(document, in_memory=True)
            if downloaded is None:
                raise CookieValidationError("invalid_cookie_row")
            result = await import_cookie_session(_document_bytes(downloaded), actor_id=user_id)
        except CookieValidationError as exc:
            error_key = _upload_error_key(exc.code)
        except Exception:
            error_key = "youtube_sessions.error_generic"
        finally:
            deleted = await _delete_upload_message(message)

        if error_key is not None:
            outcome_text = t(_LANG, error_key)
            if not deleted:
                outcome_text += "\n\n" + t(_LANG, "youtube_sessions.upload_delete_warning")
            retryable = error_key in {
                "youtube_sessions.error_file_too_large",
                "youtube_sessions.error_invalid_encoding",
                "youtube_sessions.error_invalid_netscape_header",
                "youtube_sessions.error_invalid_cookie_row",
                "youtube_sessions.error_no_youtube_cookie_rows",
                "youtube_sessions.error_duplicate_cookie",
            }
            await deliver_ask_outcome(
                client,
                chat_id,
                user_id,
                outcome_text,
                _upload_prompt_kb() if retryable else _upload_done_kb(),
                query_message=query.message,
            )
            if retryable:
                continue
            return

        if result is not None:
            key = "youtube_sessions.upload_success" if result.test.ok else "youtube_sessions.upload_test_failed"
            outcome_text = t(_LANG, key, session_id=result.session_id)
        else:
            outcome_text = t(_LANG, "youtube_sessions.error_generic")
        if not deleted:
            outcome_text += "\n\n" + t(_LANG, "youtube_sessions.upload_delete_warning")
        await deliver_ask_outcome(
            client,
            chat_id,
            user_id,
            outcome_text,
            _upload_done_kb(),
            query_message=query.message,
        )
        return


def register(bot: Client, call_py) -> None:  # noqa: ARG001
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_YOUTUBE_SESSIONS']}$") & _pm_owner, group=PANEL_CALLBACK_GROUP)
    @owner_or_above
    async def youtube_sessions_from_developer(client: Client, query: CallbackQuery):
        await query.answer()
        await _render_home(query)

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_YOUTUBE_SESSIONS']}$") & _pm_owner, group=PANEL_CALLBACK_GROUP)
    @owner_or_above
    async def youtube_sessions_from_owner(client: Client, query: CallbackQuery):
        await query.answer()
        await _render_home(query)

    # Identity-only filter (panel root): a deny-capable filter would skip this
    # handler on a stale message and leak the tap to unknown_callback. Role is
    # enforced by @owner_or_above; scope/staleness answered explicitly.
    @bot.on_callback_query(filters.regex(f"^{CB['YT_SESSION_HOME']}$"), group=PANEL_CALLBACK_GROUP)
    @owner_or_above
    async def youtube_sessions_home(client: Client, query: CallbackQuery):
        chat = query.message.chat if query.message else None
        if chat is None or getattr(chat.type, "value", chat.type) != "private":
            await query.answer(
                t(_LANG, "common.errors.unknown_callback"), show_alert=True
            )
            return
        await query.answer()
        await _render_home(query)

    @bot.on_callback_query(filters.regex(r"^yts:list:\d+$") & _pm_owner, group=PANEL_CALLBACK_GROUP)
    @owner_or_above
    async def youtube_sessions_list(client: Client, query: CallbackQuery):
        page = _parse_page(query.data, CB["YT_SESSION_LIST_PREFIX"])
        if page is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer()
        await _render_list(query, page)

    @bot.on_callback_query(filters.regex(r"^yts:detail:\d+:\d+$") & _pm_owner, group=PANEL_CALLBACK_GROUP)
    @owner_or_above
    async def youtube_sessions_detail(client: Client, query: CallbackQuery):
        parsed = _parse_session_page(query.data, CB["YT_SESSION_DETAIL_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer()
        await _render_detail(query, *parsed)

    @bot.on_callback_query(filters.regex(f"^{CB['YT_SESSION_ADD']}$") & _pm_owner, group=PANEL_CALLBACK_GROUP)
    @owner_or_above
    async def youtube_sessions_add(client: Client, query: CallbackQuery):
        await query.answer()
        await _ask_for_cookie_file(client, query)

    @bot.on_callback_query(filters.regex(r"^yts:test:\d+:\d+$") & _pm_owner, group=PANEL_CALLBACK_GROUP)
    @owner_or_above
    async def youtube_sessions_test(client: Client, query: CallbackQuery):
        parsed = _parse_session_page(query.data, CB["YT_SESSION_TEST_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        session_id, page = parsed
        await query.answer()
        result = await test_session(session_id, actor_id=query.from_user.id)
        if result.status is None:
            await _render_home(
                query,
                notice=t(_LANG, "youtube_sessions.not_found"),
            )
        elif result.ok:
            await _render_detail(
                query,
                session_id,
                page,
                notice=t(_LANG, "youtube_sessions.test_success"),
            )
        else:
            await _render_detail(
                query,
                session_id,
                page,
                notice=t(
                    _LANG,
                    "youtube_sessions.test_failed",
                    code=result.error_code or "unknown",
                ),
            )

    @bot.on_callback_query(filters.regex(r"^yts:(?:enable|disable):\d+:\d+$") & _pm_owner, group=PANEL_CALLBACK_GROUP)
    @owner_or_above
    async def youtube_sessions_set_status(client: Client, query: CallbackQuery):
        prefix = CB["YT_SESSION_ENABLE_PREFIX"] if query.data.startswith(CB["YT_SESSION_ENABLE_PREFIX"]) else CB["YT_SESSION_DISABLE_PREFIX"]
        parsed = _parse_session_page(query.data, prefix)
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        session_id, page = parsed
        requested = "active" if prefix == CB["YT_SESSION_ENABLE_PREFIX"] else "disabled"
        row = await youtube_session_repo.set_status(session_id, status=requested, actor_id=query.from_user.id)
        if row is None:
            await query.answer(t(_LANG, "youtube_sessions.not_found"), show_alert=True)
            return
        if row.status != requested:
            await query.answer(t(_LANG, "youtube_sessions.enable_invalid"), show_alert=True)
            return
        await query.answer(t(_LANG, "youtube_sessions.status_saved"))
        await _render_detail(query, session_id, page)

    @bot.on_callback_query(filters.regex(f"^{CB['YT_SESSION_REKEY_PROMPT']}$") & _pm_owner, group=PANEL_CALLBACK_GROUP)
    @owner_or_above
    async def youtube_sessions_rekey_prompt(client: Client, query: CallbackQuery):
        if not await _can_manage(query.from_user.id):
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if not has_rotation_keys():
            await query.answer(t(_LANG, "youtube_sessions.rekey_not_ready"), show_alert=True)
            return
        await query.answer()
        await query.message.edit_text(
            t(_LANG, "youtube_sessions.rekey_prompt"),
            reply_markup=KeyboardFactory.youtube_sessions_rekey_confirm(
                _LANG,
                user_id=query.from_user.id,
                issued_at=int(time.time()),
            ),
        )

    @bot.on_callback_query(filters.regex(r"^yts:rekey:(?:confirm|cancel):\d+:\d+$") & _pm_owner, group=PANEL_CALLBACK_GROUP)
    @owner_or_above
    async def youtube_sessions_rekey_confirm(client: Client, query: CallbackQuery):
        prefix = (
            CB["YT_SESSION_REKEY_CONFIRM_PREFIX"]
            if query.data.startswith(CB["YT_SESSION_REKEY_CONFIRM_PREFIX"])
            else CB["YT_SESSION_REKEY_CANCEL_PREFIX"]
        )
        parsed = _parse_rekey_confirmation(query.data, prefix)
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        user_id, issued_at = parsed
        if query.from_user.id != user_id:
            await query.answer(t(_LANG, "youtube_sessions.rekey_wrong_user"), show_alert=True)
            return
        if not await _can_manage(user_id):
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if int(time.time()) - issued_at > _DELETE_CONFIRM_TTL_SECONDS:
            await query.answer(t(_LANG, "youtube_sessions.rekey_expired"), show_alert=True)
            return
        if prefix == CB["YT_SESSION_REKEY_CANCEL_PREFIX"]:
            await query.answer(t(_LANG, "common.cancelled"))
            await _render_home(query)
            return
        if not has_rotation_keys():
            await query.answer(t(_LANG, "youtube_sessions.rekey_not_ready"), show_alert=True)
            return
        if not await _claim_rekey_confirmation(user_id, issued_at):
            await query.answer(t(_LANG, "youtube_sessions.rekey_expired"), show_alert=True)
            return

        await query.answer()
        try:
            result = await rekey_session_pool(actor_id=user_id)
        except CookieValidationError:
            await _render_home(
                query,
                notice=t(_LANG, "youtube_sessions.rekey_not_ready"),
            )
            return
        except Exception:
            await _render_home(
                query,
                notice=t(_LANG, "youtube_sessions.error_generic"),
            )
            return
        if result.busy:
            await _render_home(
                query,
                notice=t(_LANG, "youtube_sessions.rekey_busy"),
            )
            return
        key = "youtube_sessions.rekey_success" if result.failed == 0 else "youtube_sessions.rekey_partial"
        await query.message.edit_text(
            t(_LANG, key, succeeded=result.succeeded, failed=result.failed),
            reply_markup=KeyboardFactory.youtube_sessions_home(
                _LANG,
                back_callback=_back_callback(user_id),
                rekey_available=has_rotation_keys(),
            ),
        )

    @bot.on_callback_query(filters.regex(r"^yts:del:prompt:\d+:\d+$") & _pm_owner, group=PANEL_CALLBACK_GROUP)
    @owner_or_above
    async def youtube_sessions_delete_prompt(client: Client, query: CallbackQuery):
        parsed = _parse_session_page(query.data, CB["YT_SESSION_DELETE_PROMPT_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        session_id, page = parsed
        if await youtube_session_repo.get_session(session_id) is None:
            await query.answer(t(_LANG, "youtube_sessions.not_found"), show_alert=True)
            return
        await query.answer()
        await query.message.edit_text(
            t(_LANG, "youtube_sessions.delete_prompt", session_id=session_id),
            reply_markup=KeyboardFactory.youtube_session_delete_confirm(
                _LANG,
                session_id=session_id,
                page=page,
                user_id=query.from_user.id,
                issued_at=int(time.time()),
            ),
        )

    @bot.on_callback_query(filters.regex(r"^yts:del:(?:confirm|cancel):\d+:\d+:\d+:\d+$") & _pm_owner, group=PANEL_CALLBACK_GROUP)
    @owner_or_above
    async def youtube_sessions_delete_confirm(client: Client, query: CallbackQuery):
        prefix = CB["YT_SESSION_DELETE_CONFIRM_PREFIX"] if query.data.startswith(CB["YT_SESSION_DELETE_CONFIRM_PREFIX"]) else CB["YT_SESSION_DELETE_CANCEL_PREFIX"]
        parsed = _parse_delete_confirmation(query.data, prefix)
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        session_id, page, user_id, issued_at = parsed
        if query.from_user.id != user_id:
            await query.answer(t(_LANG, "youtube_sessions.delete_wrong_user"), show_alert=True)
            return
        if int(time.time()) - issued_at > _DELETE_CONFIRM_TTL_SECONDS:
            await query.answer(t(_LANG, "youtube_sessions.delete_expired"), show_alert=True)
            return
        if prefix == CB["YT_SESSION_DELETE_CANCEL_PREFIX"]:
            await query.answer(t(_LANG, "common.cancelled"))
            await _render_detail(query, session_id, page)
            return
        deleted = await youtube_session_repo.delete_session(session_id, actor_id=user_id)
        await query.answer(t(_LANG, "youtube_sessions.delete_done" if deleted else "youtube_sessions.not_found"), show_alert=not deleted)
        await _render_list(query, page)
