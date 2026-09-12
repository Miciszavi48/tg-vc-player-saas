"""Private Owner/Developer Kurigram panel for Fast-Creat API tokens."""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from pyromod.exceptions import ListenerStopped

from app.handlers.priority import PANEL_CALLBACK_GROUP
from app.repositories import fast_creat_token_repo, user_repo
from app.services.fast_creat_token_service import (
    FastCreatTokenError,
    add_token,
    has_required_keys,
)
from app.utils.ask_result import (
    deliver_ask_outcome,
    prompt_for_panel_input,
    safe_delete_user_input,
    safe_stop_listening,
)
from app.utils.bot_guards import is_developer
from app.utils.cache import get_redis
from app.utils.decorators import owner_or_above
from app.utils.filters import owner_filter, private_chat_filter
from app.utils.i18n import t
from app.utils.redis_keys import (
    TTL_FAST_CREAT_TOKEN_DELETE_CONFIRM,
    fast_creat_token_delete_claim_key,
)
from app.utils.ui import CB, KeyboardFactory, compatible_inline_button


_LANG = "fa"
_ASK_TIMEOUT_SECONDS = 120
_DELETE_CONFIRM_TTL_SECONDS = 300
_PAGE_SIZE = 8
_pm_owner = owner_filter() & private_chat_filter()


def _valid_provider(provider: str) -> str | None:
    normalized = str(provider).strip().lower()
    return normalized if normalized in fast_creat_token_repo.PROVIDERS else None


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return t(_LANG, "fast_creat_tokens.value_none")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _status_label(row) -> str:
    now = datetime.now(timezone.utc)
    cooldown_until = row.cooldown_until
    if cooldown_until is not None and cooldown_until.tzinfo is None:
        cooldown_until = cooldown_until.replace(tzinfo=timezone.utc)
    if row.status == "active" and cooldown_until and cooldown_until > now:
        return t(_LANG, "fast_creat_tokens.status_cooldown", until=_format_datetime(cooldown_until))
    return t(_LANG, f"fast_creat_tokens.status_{row.status}")


async def _can_manage(user_id: int | None) -> bool:
    return bool(user_id is not None and (is_developer(user_id) or await user_repo.is_owner(user_id)))


def _back_callback(user_id: int) -> str:
    return CB["DEV_CAT_SETTINGS"] if is_developer(user_id) else CB["NAV_BACK"]


async def _render_home(query: CallbackQuery) -> None:
    summaries = {
        provider: await fast_creat_token_repo.get_summary(provider)
        for provider in ("instagram", "tiktok", "spotify")
    }
    text = "\n\n".join([
        t(_LANG, "fast_creat_tokens.title"),
        t(
            _LANG,
            "fast_creat_tokens.summary",
            instagram_active=summaries["instagram"].active,
            instagram_total=summaries["instagram"].total,
            tiktok_active=summaries["tiktok"].active,
            tiktok_total=summaries["tiktok"].total,
            spotify_active=summaries["spotify"].active,
            spotify_total=summaries["spotify"].total,
        ),
    ])
    await query.message.edit_text(
        text,
        reply_markup=KeyboardFactory.fast_creat_tokens_home(
            _LANG,
            back_callback=_back_callback(query.from_user.id),
        ),
    )


async def _render_provider_home(query: CallbackQuery, provider: str) -> None:
    summary = await fast_creat_token_repo.get_summary(provider)
    await query.message.edit_text(
        t(
            _LANG,
            "fast_creat_tokens.provider_summary",
            provider=t(_LANG, f"fast_creat_tokens.provider_{provider}"),
            active=summary.active,
            cooldown=summary.cooldown,
            inactive=summary.disabled_or_invalid,
            total=summary.total,
        ),
        reply_markup=KeyboardFactory.fast_creat_tokens_provider_home(_LANG, provider=provider),
    )


async def _render_list(query: CallbackQuery, provider: str, page: int) -> None:
    rows, total = await fast_creat_token_repo.list_tokens(provider, page=page, page_size=_PAGE_SIZE)
    total_pages = max(1, math.ceil(total / _PAGE_SIZE))
    page = min(max(0, page), total_pages - 1)
    if not rows and total:
        rows, total = await fast_creat_token_repo.list_tokens(provider, page=page, page_size=_PAGE_SIZE)
    if not rows:
        await query.message.edit_text(
            t(_LANG, "fast_creat_tokens.list_empty"),
            reply_markup=KeyboardFactory.fast_creat_tokens_provider_home(_LANG, provider=provider),
        )
        return
    await query.message.edit_text(
        t(_LANG, "fast_creat_tokens.list_title", provider=t(_LANG, f"fast_creat_tokens.provider_{provider}")),
        reply_markup=KeyboardFactory.fast_creat_tokens_list(
            _LANG,
            [(row.id, _status_label(row)) for row in rows],
            provider=provider,
            page=page,
            total_pages=total_pages,
        ),
    )


async def _render_detail(query: CallbackQuery, provider: str, token_id: int, page: int) -> None:
    row = await fast_creat_token_repo.get_token(token_id)
    if row is None or row.provider != provider:
        await query.answer(t(_LANG, "fast_creat_tokens.not_found"), show_alert=True)
        return
    await query.message.edit_text(
        t(
            _LANG,
            "fast_creat_tokens.detail",
            token_id=row.id,
            provider=t(_LANG, f"fast_creat_tokens.provider_{provider}"),
            status=_status_label(row),
            selected=_format_datetime(row.last_selected_at),
            success=_format_datetime(row.last_success_at),
            error=row.last_error_code or t(_LANG, "fast_creat_tokens.value_none"),
            uses=row.use_count,
            failures=row.failure_count,
        ),
        reply_markup=KeyboardFactory.fast_creat_token_detail(
            _LANG,
            provider=provider,
            token_id=token_id,
            status=row.status,
            page=page,
        ),
    )


def _parse_provider_page(data: str, prefix: str) -> tuple[str, int] | None:
    payload = data.removeprefix(prefix)
    provider, separator, page_text = payload.partition(":")
    provider = _valid_provider(provider)
    if not separator or provider is None:
        return None
    try:
        page = int(page_text)
    except ValueError:
        return None
    return (provider, page) if page >= 0 else None


def _parse_provider_token_page(data: str, prefix: str) -> tuple[str, int, int] | None:
    payload = data.removeprefix(prefix)
    parts = payload.split(":")
    if len(parts) != 3:
        return None
    provider = _valid_provider(parts[0])
    try:
        token_id, page = int(parts[1]), int(parts[2])
    except ValueError:
        return None
    if provider is None or token_id <= 0 or page < 0:
        return None
    return provider, token_id, page


def _parse_delete_confirmation(data: str, prefix: str) -> tuple[str, int, int, int, int] | None:
    payload = data.removeprefix(prefix)
    parts = payload.split(":")
    if len(parts) != 5:
        return None
    provider = _valid_provider(parts[0])
    try:
        token_id, page, user_id, issued_at = (int(value) for value in parts[1:])
    except ValueError:
        return None
    if provider is None or token_id <= 0 or page < 0 or user_id <= 0 or issued_at <= 0:
        return None
    return provider, token_id, page, user_id, issued_at


async def _claim_delete_confirmation(token_id: int, user_id: int, issued_at: int) -> bool:
    if not _confirmation_is_fresh(issued_at):
        return False
    try:
        redis = await get_redis()
        claimed = await redis.set(
            fast_creat_token_delete_claim_key(token_id, user_id, issued_at),
            "1",
            nx=True,
            ex=TTL_FAST_CREAT_TOKEN_DELETE_CONFIRM,
        )
    except Exception:
        return False
    return bool(claimed)


def _confirmation_is_fresh(issued_at: int, *, now: int | None = None) -> bool:
    age = (int(time.time()) if now is None else now) - issued_at
    return 0 <= age <= _DELETE_CONFIRM_TTL_SECONDS


def _token_prompt_kb(provider: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            compatible_inline_button(
                t(_LANG, "common.buttons.cancel_inline"),
                callback_data=f"{CB['FAST_CREAT_PROVIDER_PREFIX']}{provider}",
            )
        ]]
    )


def _token_done_kb(provider: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                t(_LANG, "common.buttons.back"),
                callback_data=f"{CB['FAST_CREAT_PROVIDER_PREFIX']}{provider}",
            )
        ]]
    )


async def _ask_for_token(client: Client, query: CallbackQuery, provider: str) -> None:
    user_id = query.from_user.id
    chat_id = query.message.chat.id
    await safe_stop_listening(client, chat_id, user_id=user_id)
    while True:
        submitted = None
        try:
            submitted = await prompt_for_panel_input(
                client,
                chat_id,
                user_id,
                t(_LANG, "fast_creat_tokens.add_prompt", provider=t(_LANG, f"fast_creat_tokens.provider_{provider}")),
                _token_prompt_kb(provider),
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
                _token_done_kb(provider),
                query_message=query.message,
            )
            return

        if not await _can_manage(user_id):
            await safe_delete_user_input(submitted)
            await deliver_ask_outcome(
                client,
                chat_id,
                user_id,
                t(_LANG, "common.errors.no_access"),
                _token_done_kb(provider),
                query_message=query.message,
            )
            return

        deleted = True
        try:
            value = str(getattr(submitted, "text", "") or "")
            token_id = await add_token(provider=provider, value=value, actor_id=user_id)
        except FastCreatTokenError as exc:
            key = {
                "storage_not_configured": "fast_creat_tokens.storage_not_configured",
                "duplicate_token": "fast_creat_tokens.duplicate_token",
                "invalid_token": "fast_creat_tokens.invalid_token",
            }.get(exc.code, "fast_creat_tokens.error_generic")
            deleted = await safe_delete_user_input(submitted)
            outcome_text = t(_LANG, key)
            if not deleted:
                outcome_text += "\n\n" + t(_LANG, "fast_creat_tokens.delete_warning")
            retryable = exc.code in {"duplicate_token", "invalid_token"}
            await deliver_ask_outcome(
                client,
                chat_id,
                user_id,
                outcome_text,
                _token_prompt_kb(provider) if retryable else _token_done_kb(provider),
                query_message=query.message,
            )
            if retryable:
                continue
            return
        except Exception:
            deleted = await safe_delete_user_input(submitted)
            outcome_text = t(_LANG, "fast_creat_tokens.error_generic")
            if not deleted:
                outcome_text += "\n\n" + t(_LANG, "fast_creat_tokens.delete_warning")
            await deliver_ask_outcome(
                client,
                chat_id,
                user_id,
                outcome_text,
                _token_done_kb(provider),
                query_message=query.message,
            )
            return

        deleted = await safe_delete_user_input(submitted)
        success_text = t(_LANG, "fast_creat_tokens.added", token_id=token_id)
        if not deleted:
            success_text += "\n\n" + t(_LANG, "fast_creat_tokens.delete_warning")
        await deliver_ask_outcome(
            client,
            chat_id,
            user_id,
            success_text,
            _token_done_kb(provider),
            query_message=query.message,
        )
        return


def register(bot: Client, call_py) -> None:  # noqa: ARG001
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_FAST_CREAT_TOKENS']}$") & _pm_owner, group=PANEL_CALLBACK_GROUP)
    @owner_or_above
    async def fast_creat_tokens_from_developer(client: Client, query: CallbackQuery):
        await query.answer()
        await _render_home(query)

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_FAST_CREAT_TOKENS']}$") & _pm_owner, group=PANEL_CALLBACK_GROUP)
    @owner_or_above
    async def fast_creat_tokens_from_owner(client: Client, query: CallbackQuery):
        await query.answer()
        await _render_home(query)

    # Identity-only filter (panel root): a deny-capable filter would skip this
    # handler on a stale message and leak the tap to unknown_callback. Role is
    # enforced by @owner_or_above; scope/staleness answered explicitly.
    @bot.on_callback_query(filters.regex(f"^{CB['FAST_CREAT_HOME']}$"), group=PANEL_CALLBACK_GROUP)
    @owner_or_above
    async def fast_creat_tokens_home(client: Client, query: CallbackQuery):
        chat = query.message.chat if query.message else None
        if chat is None or getattr(chat.type, "value", chat.type) != "private":
            await query.answer(
                t(_LANG, "common.errors.unknown_callback"), show_alert=True
            )
            return
        await query.answer()
        await _render_home(query)

    @bot.on_callback_query(filters.regex(r"^fct:provider:(?:instagram|tiktok|spotify)$") & _pm_owner, group=PANEL_CALLBACK_GROUP)
    @owner_or_above
    async def fast_creat_tokens_provider(client: Client, query: CallbackQuery):
        provider = _valid_provider(query.data.removeprefix(CB["FAST_CREAT_PROVIDER_PREFIX"]))
        if provider is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer()
        await _render_provider_home(query, provider)

    @bot.on_callback_query(filters.regex(r"^fct:add:(?:instagram|tiktok|spotify)$") & _pm_owner, group=PANEL_CALLBACK_GROUP)
    @owner_or_above
    async def fast_creat_tokens_add(client: Client, query: CallbackQuery):
        provider = _valid_provider(query.data.removeprefix(CB["FAST_CREAT_ADD_PREFIX"]))
        if provider is None or not await _can_manage(query.from_user.id):
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if not has_required_keys():
            await query.answer(t(_LANG, "fast_creat_tokens.storage_not_configured"), show_alert=True)
            return
        await query.answer()
        await _ask_for_token(client, query, provider)

    @bot.on_callback_query(filters.regex(r"^fct:list:(?:instagram|tiktok|spotify):\d+$") & _pm_owner, group=PANEL_CALLBACK_GROUP)
    @owner_or_above
    async def fast_creat_tokens_list(client: Client, query: CallbackQuery):
        parsed = _parse_provider_page(query.data, CB["FAST_CREAT_LIST_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer()
        await _render_list(query, *parsed)

    @bot.on_callback_query(filters.regex(r"^fct:detail:(?:instagram|tiktok|spotify):\d+:\d+$") & _pm_owner, group=PANEL_CALLBACK_GROUP)
    @owner_or_above
    async def fast_creat_tokens_detail(client: Client, query: CallbackQuery):
        parsed = _parse_provider_token_page(query.data, CB["FAST_CREAT_DETAIL_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer()
        await _render_detail(query, *parsed)

    @bot.on_callback_query(filters.regex(r"^fct:(?:enable|disable):(?:instagram|tiktok|spotify):\d+:\d+$") & _pm_owner, group=PANEL_CALLBACK_GROUP)
    @owner_or_above
    async def fast_creat_tokens_set_status(client: Client, query: CallbackQuery):
        prefix = CB["FAST_CREAT_ENABLE_PREFIX"] if query.data.startswith(CB["FAST_CREAT_ENABLE_PREFIX"]) else CB["FAST_CREAT_DISABLE_PREFIX"]
        parsed = _parse_provider_token_page(query.data, prefix)
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        provider, token_id, page = parsed
        if not await _can_manage(query.from_user.id):
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        current = await fast_creat_token_repo.get_token(token_id)
        if current is None or current.provider != provider:
            await query.answer(t(_LANG, "fast_creat_tokens.not_found"), show_alert=True)
            return
        requested = "active" if prefix == CB["FAST_CREAT_ENABLE_PREFIX"] else "disabled"
        row = await fast_creat_token_repo.set_status(token_id, status=requested, actor_id=query.from_user.id)
        if row is None:
            await query.answer(t(_LANG, "fast_creat_tokens.not_found"), show_alert=True)
            return
        await query.answer(t(_LANG, "fast_creat_tokens.status_saved"))
        await _render_detail(query, provider, token_id, page)

    @bot.on_callback_query(filters.regex(r"^fct:del:prompt:(?:instagram|tiktok|spotify):\d+:\d+$") & _pm_owner, group=PANEL_CALLBACK_GROUP)
    @owner_or_above
    async def fast_creat_tokens_delete_prompt(client: Client, query: CallbackQuery):
        parsed = _parse_provider_token_page(query.data, CB["FAST_CREAT_DELETE_PROMPT_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        provider, token_id, page = parsed
        row = await fast_creat_token_repo.get_token(token_id)
        if row is None or row.provider != provider:
            await query.answer(t(_LANG, "fast_creat_tokens.not_found"), show_alert=True)
            return
        await query.answer()
        await query.message.edit_text(
            t(_LANG, "fast_creat_tokens.delete_prompt", token_id=token_id),
            reply_markup=KeyboardFactory.fast_creat_token_delete_confirm(
                _LANG,
                provider=provider,
                token_id=token_id,
                page=page,
                user_id=query.from_user.id,
                issued_at=int(time.time()),
            ),
        )

    @bot.on_callback_query(filters.regex(r"^fct:del:(?:confirm|cancel):(?:instagram|tiktok|spotify):\d+:\d+:\d+:\d+$") & _pm_owner, group=PANEL_CALLBACK_GROUP)
    @owner_or_above
    async def fast_creat_tokens_delete_confirm(client: Client, query: CallbackQuery):
        prefix = CB["FAST_CREAT_DELETE_CONFIRM_PREFIX"] if query.data.startswith(CB["FAST_CREAT_DELETE_CONFIRM_PREFIX"]) else CB["FAST_CREAT_DELETE_CANCEL_PREFIX"]
        parsed = _parse_delete_confirmation(query.data, prefix)
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        provider, token_id, page, user_id, issued_at = parsed
        if query.from_user.id != user_id:
            await query.answer(t(_LANG, "fast_creat_tokens.delete_wrong_user"), show_alert=True)
            return
        if not await _can_manage(user_id):
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if not _confirmation_is_fresh(issued_at):
            await query.answer(t(_LANG, "fast_creat_tokens.delete_expired"), show_alert=True)
            return
        if prefix == CB["FAST_CREAT_DELETE_CANCEL_PREFIX"]:
            await query.answer(t(_LANG, "common.cancelled"))
            await _render_detail(query, provider, token_id, page)
            return
        if not await _claim_delete_confirmation(token_id, user_id, issued_at):
            await query.answer(t(_LANG, "fast_creat_tokens.delete_expired"), show_alert=True)
            return
        row = await fast_creat_token_repo.get_token(token_id)
        if row is None or row.provider != provider:
            await query.answer(t(_LANG, "fast_creat_tokens.not_found"), show_alert=True)
            return
        deleted = await fast_creat_token_repo.delete_token(token_id, actor_id=user_id)
        await query.answer(t(_LANG, "fast_creat_tokens.delete_done" if deleted else "fast_creat_tokens.not_found"), show_alert=not deleted)
        await _render_list(query, provider, page)
