from __future__ import annotations

import logging
import re
import secrets
import time
from collections.abc import Callable
from datetime import datetime, timezone

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from pyromod.exceptions import ListenerStopped

from app.repositories import (
    admin_report_repo,
    blacklist_repo,
    channel_repo,
    filter_repo,
    force_join_repo,
    group_repo,
    log_repo,
    monthly_invoice_repo,
    settings_repo,
    user_repo,
)
from app.handlers.broadcast_wizard import begin_broadcast_wizard
from app.handlers.broadcast_panel import render_broadcast_history_panel
from app.handlers.analytics_panel import render_analytics_home_panel
from app.handlers.help_center import render_help_home_panel
from app.handlers.helper_panel import render_helper_home_panel
from app.handlers.priority import REMOVED_FINANCIAL_CALLBACK_GROUP
from app.services import CallService, CreditService, InstallPolicyService, monthly_invoice_service
from app.services import start_customization_service as start_custom
from app.services.admin_dashboard_service import AdminDashboardService
from app.services.bot_settings_service import get_about_text
from app.services.admin_title_service import (
    apply_admin_title,
    preflight_admin_title_apply,
    preflight_manual_telegram_promotion,
    promote_telegram_admin,
)
from app.services.forced_membership_service import ForcedMembershipService
from app.services.media_health_service import (
    build_cleanup_preview,
    build_health_snapshot,
    build_media_report,
    execute_safe_cleanup,
    format_cleanup_preview,
    format_cleanup_result,
    format_health_report,
    format_media_ranking_report,
    write_media_report_tempfile,
)
from app.services.media_event_service import get_media_ranking_summary
from app.services.bot_update_service import (
    build_diagnostics_snapshot,
    execute_configured_reload,
    format_bot_update_panel,
    reload_is_available,
)
from app.services.notification_service import NotificationService
from app.services.panel_message_service import (
    panel_callback_edit,
    remember_panel_from_query,
    replace_panel_with_photo,
)
from app.utils.ask_result import (
    AskResult,
    deliver_ask_outcome,
    notify_ask_abort,
    prompt_for_panel_input,
    safe_delete_user_input,
    safe_stop_listening,
)
from app.services.texts_links_ui import (
    all_field_keys,
    build_text_field_payload,
    build_texts_hub_payload,
    confirmation_key_for_clear,
    confirmation_key_for_save,
    decode_setting_value,
    encode_media_value,
    extract_media_payload,
    field_from_callback,
    get_field_spec,
    is_valid_link_value,
    normalize_link_value,
)
from app.services.wizard_ui import (
    TOKEN_DEV_BROADCAST,
    TOKEN_DEV_CREDIT,
    TOKEN_DEV_FORCE_JOIN,
    TOKEN_DEV_INSTALL_POLICY,
    TOKEN_DEV_LISTS,
    TOKEN_DEV_MONTHLY_INVOICE,
    TOKEN_DEV_MODERATION,
    TOKEN_DEV_RATES,
    TOKEN_DEV_SETTINGS,
    TOKEN_DEV_TEXTS,
    TOKEN_DEV_USERS,
    TOKEN_ROLE_ROOT,
    build_cancel_kb,
    build_done_kb,
    cancel_and_resolve,
    pop_return_token,
    remember_return_token,
)
from app.utils.cache import get_redis, invalidate_filterwords, invalidate_ownerlist, invalidate_sudolist
from app.utils.button_style import mark_toggle_state
from app.utils.decorators import developer_only
from app.utils.filters import dev_filter, private_chat_filter
from app.utils.helpers import (
    MAX_CREDIT_DAYS,
    MAX_LIMIT_VALUE,
    MAX_RATE_VALUE,
    MAX_WALLET_AMOUNT,
    parse_bounded_int,
    parse_user_id,
)
from app.utils.i18n import label, t
from app.utils.ui import CB, KeyboardFactory, compatible_inline_button
from app.utils.redis_keys import instance_key

logger = logging.getLogger(__name__)

_LANG = "fa"
_TIMEOUT = 60
_FJ_REMOVE_CONFIRM_TTL_SECONDS = 300
_DEV_CONFIRM_TTL_SECONDS = 300

_pm_dev = dev_filter() & private_chat_filter()
_ADMIN_TITLE_MAX_LEN = 32
_ADMIN_TITLE_URL_RE = re.compile(r"https?://|t\.me/|www\.", re.IGNORECASE)
_TITLE_APPLY_PENDING_PREFIX = instance_key("dev_title_apply:")
_TGPROM_PENDING_PREFIX = instance_key("dev_tg_prom:")
async def _ask(
    client: Client,
    chat_id: int,
    key: str,
    lang: str = "fa",
    *,
    user_id: int | None = None,
    return_to: str = TOKEN_ROLE_ROOT,
    prompt_kwargs: dict[str, str] | None = None,
    prompt_text: str | None = None,
    delete_response: bool = True,
) -> AskResult:
    """Render a typed-input prompt in the active panel and wait for a reply."""
    if user_id is not None:
        await remember_return_token(user_id, return_to)

    stopped = await safe_stop_listening(client, chat_id, user_id=user_id)
    logger.debug(
        "dev ask start key=%s chat_id=%s user_id=%s stop_listening=%s",
        key,
        chat_id,
        user_id,
        stopped,
    )

    try:
        message = await prompt_for_panel_input(
            client,
            chat_id,
            user_id,
            prompt_text or t(lang, key, **(prompt_kwargs or {})),
            build_cancel_kb(lang, return_to),
            timeout=_TIMEOUT,
        )
        if message and message.text and message.text.strip().lower() in ("/cancel", "cancel", "لغو"):
            await safe_delete_user_input(message)
            if user_id is not None:
                text, kb = await cancel_and_resolve(
                    client,
                    user_id,
                    chat_id,
                    "private",
                    lang=lang,
                    return_to=return_to,
                )
                await deliver_ask_outcome(
                    client, chat_id, user_id, text, kb,
                )
            else:
                await deliver_ask_outcome(
                    client,
                    chat_id,
                    None,
                    t(lang, "common.cancelled"),
                    build_done_kb(lang, return_to),
                )
            return AskResult(message=None, abort_reason="cancel_text", user_notified=True)
        if delete_response:
            await safe_delete_user_input(message)
        if user_id is not None:
            await pop_return_token(user_id)
        return AskResult(message=message)
    except ListenerStopped:
        logger.debug("dev ask listener stopped key=%s chat_id=%s user_id=%s", key, chat_id, user_id)
        # A callback/command that stops the listener owns the next panel render.
        # Sending another "cancelled/expired" outcome here races that navigation.
        return AskResult(message=None, abort_reason="listener_stopped", user_notified=True)
    except Exception as exc:
        logger.debug(
            "dev ask failed key=%s chat_id=%s user_id=%s reason=%s",
            key,
            chat_id,
            user_id,
            type(exc).__name__,
        )
        await deliver_ask_outcome(
            client,
            chat_id,
            user_id,
            t(lang, "ask.timeout"),
            build_done_kb(lang, return_to),
        )
        if user_id is not None:
            await pop_return_token(user_id)
        return AskResult(message=None, abort_reason="timeout", user_notified=True)


async def _ask_value(
    client: Client,
    chat_id: int,
    key: str,
    parser: Callable[[object], object | None],
    invalid_text: str,
    *,
    user_id: int,
    return_to: str,
    prompt_kwargs: dict[str, str] | None = None,
) -> tuple[AskResult, object | None]:
    """Keep the same typed-input context active until syntax validates."""
    retry_prompt: str | None = None
    while True:
        result = await _ask(
            client,
            chat_id,
            key,
            user_id=user_id,
            return_to=return_to,
            prompt_kwargs=prompt_kwargs,
            prompt_text=retry_prompt,
        )
        if result.message is None:
            return result, None
        value = parser(result.message)
        if value is not None:
            return result, value
        base_prompt = t(_LANG, key, **(prompt_kwargs or {}))
        retry_prompt = f"{invalid_text}\n\n{base_prompt}"


async def _notify_texts_links_ask_abort(
    client: Client,
    chat_id: int,
    result: AskResult,
    return_to: str,
) -> bool:
    """Return True when a texts/links handler should stop after an aborted ask."""
    return await notify_ask_abort(
        client,
        chat_id,
        result,
        return_to=return_to,
        lang=_LANG,
        user_id=chat_id,
    )


async def _send_done(
    client: Client,
    chat_id: int,
    text: str,
    return_to: str,
    *,
    user_id: int | None = None,
) -> None:
    anchor_user_id = user_id if user_id is not None else chat_id
    await deliver_ask_outcome(
        client,
        chat_id,
        anchor_user_id,
        text,
        build_done_kb(_LANG, return_to),
    )


def _bool_label(enabled: bool) -> str:
    return t(_LANG, "common.labels.on") if enabled else t(_LANG, "common.labels.off")


def _summary_row(label_key: str, value: str | int) -> str:
    template = t(_LANG, "list_fmt.setting_entry")
    return template.format(
        key=t(_LANG, label_key),
        value=str(value),
    )


async def _build_dev_settings_text() -> str:
    summary = await AdminDashboardService.get_general_summary()
    rows = [
        t(_LANG, "panels.developer.cat_settings_title"),
        "",
        t(_LANG, "panels.developer.cat_settings_desc"),
        "",
    ]

    rows.append(
        t(
            _LANG,
            "status.setting_value",
            feature=t(_LANG, "status.auto_leave_label"),
            value=_bool_label(bool(summary["auto_leave_enabled"])),
        )
    )
    rows.append(
        t(
            _LANG,
            "status.setting_value",
            feature=t(_LANG, "status.trial_label"),
            value=_bool_label(bool(summary["trial_enabled"])),
        )
    )
    rows.append(
        t(
            _LANG,
            "status.setting_value",
            feature=t(_LANG, "status.bot_enabled_label"),
            value=_bool_label(bool(summary.get("bot_enabled", True))),
        )
    )
    rows.append(
        t(
            _LANG,
            "status.setting_value",
            feature=t(_LANG, "status.sudo_panel_enabled_label"),
            value=_bool_label(bool(summary.get("sudo_panel_enabled", True))),
        )
    )
    return "\n".join(rows)


async def _build_dev_credit_text() -> str:
    summary = await AdminDashboardService.get_credit_summary()
    rows = [
        t(_LANG, "panels.developer.cat_credit_title"),
        "",
        t(_LANG, "panels.developer.cat_credit_desc"),
        "",
        _summary_row("panels.developer.summary_active_installs", summary["active_installs"]),
        _summary_row("panels.developer.summary_expiring_24h", summary["expiring_24h"]),
        _summary_row("panels.developer.summary_no_credit", summary["no_credit"]),
        _summary_row("panels.developer.summary_invoices_24h", summary["invoices_24h"]),
    ]
    return "\n".join(rows)


async def _build_dev_lists_text() -> str:
    summary = await AdminDashboardService.get_lists_summary()
    rows = [
        t(_LANG, "panels.developer.cat_lists_title"),
        "",
        t(_LANG, "panels.developer.cat_lists_desc"),
        "",
        _summary_row("panels.developer.summary_groups", summary["groups"]),
        _summary_row("panels.developer.summary_channels", summary["channels"]),
        _summary_row("panels.developer.summary_no_credit", summary["no_credit"]),
        _summary_row("panels.developer.summary_expiring_24h", summary["expiring_24h"]),
    ]
    return "\n".join(rows)


async def _build_dev_users_text() -> str:
    summary = await AdminDashboardService.get_users_summary()
    rows = [
        t(_LANG, "panels.developer.cat_users_title"),
        "",
        t(_LANG, "panels.developer.cat_users_desc"),
        "",
        _summary_row("panels.developer.summary_owners", summary["owners"]),
        _summary_row("panels.developer.summary_sudos", summary["sudos"]),
    ]
    return "\n".join(rows)


async def _build_dev_force_join_text() -> str:
    summary = await AdminDashboardService.get_general_summary()
    rows = [
        t(_LANG, "panels.developer.cat_force_join_title"),
        "",
        t(_LANG, "panels.developer.cat_force_join_desc"),
        "",
        _summary_row("status.force_join_label", _bool_label(bool(summary["force_join_enabled"]))),
        _summary_row("panels.developer.summary_required_channels", int(summary["required_channels"])),
    ]
    return "\n".join(rows)


async def _build_dev_moderation_text() -> str:
    return "\n".join(
        [
            t(_LANG, "panels.developer.cat_moderation_title"),
            "",
            t(_LANG, "panels.developer.cat_moderation_desc"),
        ]
    )


async def _build_dev_texts_text() -> str:
    total = 0
    configured = 0
    media = 0
    for key in all_field_keys():
        total += 1
        parsed = decode_setting_value(await settings_repo.get_bot_setting(key))
        if parsed.get("mode") == "empty":
            continue
        configured += 1
        if parsed.get("mode") == "media":
            media += 1

    lines = [
        t(_LANG, "panels.developer.cat_texts_title"),
        "",
        t(_LANG, "panels.developer.cat_texts_desc"),
        "",
        _summary_row("panels.developer.summary_text_fields_total", total),
        _summary_row("panels.developer.summary_text_fields_set", configured),
        _summary_row("panels.developer.summary_text_fields_media", media),
    ]
    return "\n".join(lines)


async def _build_dev_monthly_invoice_text() -> str:
    period = monthly_invoice_service.calculate_calendar_month_period()
    amount = await monthly_invoice_service.get_monthly_invoice_amount()
    owners = await monthly_invoice_service.get_active_owner_recipients()
    auto_send_enabled = await monthly_invoice_service.get_monthly_invoice_auto_send_enabled()
    amount_text = (
        str(amount)
        if amount is not None
        else t(_LANG, "monthly_invoice.amount_value_unset")
    )
    auto_send_status = t(
        _LANG,
        "monthly_invoice.auto_send_enabled_label"
        if auto_send_enabled
        else "monthly_invoice.auto_send_disabled_label",
    )
    return t(
        _LANG,
        "monthly_invoice.config_summary",
        amount=amount_text,
        period_start=period.period_start.isoformat(),
        period_end=period.period_end.isoformat(),
        due_date=period.due_at.date().isoformat(),
        owner_count=len(owners),
        auto_send_status=auto_send_status,
    )


async def _dev_monthly_invoice_keyboard() -> InlineKeyboardMarkup:
    enabled = await monthly_invoice_service.get_monthly_invoice_auto_send_enabled()
    return KeyboardFactory.dev_sub_monthly_invoice(_LANG, auto_send_enabled=enabled)


def _format_monthly_invoice_dt(value) -> str:
    if value is None:
        return "-"
    if hasattr(value, "date"):
        return value.date().isoformat()
    return str(value)


def _monthly_invoice_detail_cb(invoice_id: int) -> str:
    return f"{CB['DEV_MONTHLY_INVOICE_DETAIL_PREFIX']}{int(invoice_id)}"


def _trim_monthly_invoice_panel_text(text: str) -> str:
    max_len = 3900
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _render_monthly_invoice_prepare_result(result: monthly_invoice_service.MonthlyInvoiceBuildResult) -> str:
    if not result.amount_configured:
        return "\n\n".join(
            [
                t(_LANG, "monthly_invoice.prepare_title"),
                result.skipped_reason or t(_LANG, "monthly_invoice.amount_not_configured"),
                t(_LANG, "monthly_invoice.sending_disabled_note"),
            ]
        )
    if not result.previews:
        return "\n\n".join(
            [
                t(_LANG, "monthly_invoice.prepare_title"),
                t(_LANG, "monthly_invoice.no_active_owners"),
                t(_LANG, "monthly_invoice.sending_disabled_note"),
            ]
        )

    period = result.period
    lines = [
        t(_LANG, "monthly_invoice.prepare_title"),
        "",
        t(
            _LANG,
            "monthly_invoice.prepare_result",
            count=len(result.previews),
            amount=result.amount,
            period_start=period.period_start.isoformat(),
            period_end=period.period_end.isoformat(),
            due_date=period.due_at.date().isoformat(),
        ),
        t(_LANG, "monthly_invoice.prepare_creates_records_note"),
        "",
    ]
    shown = result.previews[:3]
    for preview in shown:
        invoice = preview.invoice
        lines.append(
            t(
                _LANG,
                "monthly_invoice.preview_owner_block",
                owner_user_id=preview.owner_user_id,
                invoice_id=invoice.id,
                status=t(_LANG, f"monthly_invoice.status.{invoice.status}"),
                install_count=preview.stats.install_count,
                private_count=preview.stats.private_count,
                group_count=preview.stats.group_count,
                channel_count=preview.stats.channel_count,
            )
        )
    hidden_count = len(result.previews) - len(shown)
    if hidden_count > 0:
        lines.append(t(_LANG, "monthly_invoice.preview_more", count=hidden_count))
    lines.extend(["", t(_LANG, "monthly_invoice.preview_message_header"), "", shown[0].message])
    return _trim_monthly_invoice_panel_text("\n".join(lines))


def _render_monthly_invoice_list(invoices: list) -> tuple[str, InlineKeyboardMarkup]:
    if not invoices:
        return (
            t(_LANG, "monthly_invoice.list_empty"),
            KeyboardFactory.dev_sub_monthly_invoice(_LANG),
        )

    lines = [t(_LANG, "monthly_invoice.list_title"), ""]
    rows: list[list[InlineKeyboardButton]] = []
    for invoice in invoices:
        lines.append(
            t(
                _LANG,
                "monthly_invoice.list_item",
                id=invoice.id,
                owner_user_id=invoice.owner_user_id,
                period_start=invoice.period_start.isoformat(),
                period_end=invoice.period_end.isoformat(),
                amount=invoice.amount,
                status=t(_LANG, f"monthly_invoice.status.{invoice.status}"),
                created_at=_format_monthly_invoice_dt(invoice.created_at),
                sent_at=_format_monthly_invoice_dt(invoice.sent_at),
                paid_at=_format_monthly_invoice_dt(invoice.paid_at),
            )
        )
        rows.append([
            InlineKeyboardButton(
                t(_LANG, "monthly_invoice.detail_btn", id=invoice.id),
                callback_data=_monthly_invoice_detail_cb(invoice.id),
            )
        ])
    rows.append([
        InlineKeyboardButton(
            t(_LANG, "common.buttons.back"),
            callback_data=CB["DEV_CAT_MONTHLY_INVOICE"],
        )
    ])
    return _trim_monthly_invoice_panel_text("\n".join(lines)), InlineKeyboardMarkup(rows)


def _render_monthly_invoice_detail(invoice) -> str:
    text = t(
        _LANG,
        "monthly_invoice.detail_text",
        id=invoice.id,
        owner_user_id=invoice.owner_user_id,
        bot_identifier=invoice.bot_identifier,
        period_start=invoice.period_start.isoformat(),
        period_end=invoice.period_end.isoformat(),
        due_date=invoice.due_at.date().isoformat(),
        amount=invoice.amount,
        status=t(_LANG, f"monthly_invoice.status.{invoice.status}"),
        install_count=invoice.install_count,
        private_count=invoice.private_count,
        group_count=invoice.group_count,
        channel_count=invoice.channel_count,
        developer_id=invoice.developer_id if invoice.developer_id is not None else "-",
        created_at=_format_monthly_invoice_dt(invoice.created_at),
        sent_at=_format_monthly_invoice_dt(invoice.sent_at),
        paid_at=_format_monthly_invoice_dt(invoice.paid_at),
        delivery_error=invoice.delivery_error or "-",
    )
    rendered = monthly_invoice_service.render_invoice_message(invoice, lang=_LANG)
    return _trim_monthly_invoice_panel_text(
        "\n\n".join(
            [
                t(_LANG, "monthly_invoice.detail_title"),
                text,
                t(_LANG, "monthly_invoice.preview_message_header"),
                rendered,
            ]
        )
    )


def _render_monthly_invoice_send_result(result: monthly_invoice_service.MonthlyInvoiceDeliveryResult) -> str:
    if not result.amount_configured:
        return "\n\n".join(
            [
                t(_LANG, "monthly_invoice.send_title"),
                result.skipped_reason or t(_LANG, "monthly_invoice.amount_not_configured"),
            ]
        )
    if result.total == 0:
        return "\n\n".join(
            [
                t(_LANG, "monthly_invoice.send_title"),
                t(_LANG, "monthly_invoice.no_prepared_invoices"),
            ]
        )

    lines = [
        t(_LANG, "monthly_invoice.send_title"),
        "",
        t(
            _LANG,
            "monthly_invoice.send_result_summary",
            total=result.total,
            sent=result.sent,
            failed=result.failed,
            skipped=result.skipped,
            period_start=result.period.period_start.isoformat(),
            period_end=result.period.period_end.isoformat(),
        ),
    ]
    failed_owner_ids = [
        str(item.owner_user_id)
        for item in result.items
        if item.status == "failed"
    ]
    skipped_owner_ids = [
        str(item.owner_user_id)
        for item in result.items
        if item.status == "skipped"
    ]
    if failed_owner_ids:
        lines.append(
            t(_LANG, "monthly_invoice.send_failed_owner_ids", owner_ids=", ".join(failed_owner_ids))
        )
    if skipped_owner_ids:
        lines.append(
            t(_LANG, "monthly_invoice.send_skipped_owner_ids", owner_ids=", ".join(skipped_owner_ids))
        )
    lines.append(t(_LANG, "monthly_invoice.send_no_scheduler_note"))
    return _trim_monthly_invoice_panel_text("\n".join(lines))



async def _dev_settings_keyboard() -> InlineKeyboardMarkup:
    summary = await AdminDashboardService.get_general_summary()
    return KeyboardFactory.dev_sub_settings(
        _LANG,
        bot_enabled=bool(summary.get("bot_enabled", True)),
        sudo_panel_enabled=bool(summary.get("sudo_panel_enabled", True)),
        force_join_enabled=bool(summary.get("force_join_enabled", False)),
        auto_leave_enabled=bool(summary.get("auto_leave_enabled", False)),
        trial_enabled=bool(summary.get("trial_enabled", False)),
    )


async def _dev_force_join_keyboard() -> InlineKeyboardMarkup:
    summary = await AdminDashboardService.get_general_summary()
    return KeyboardFactory.dev_sub_force_join(
        _LANG,
        force_join_enabled=bool(summary.get("force_join_enabled", False)),
    )


async def _show_dev_settings_menu(query: CallbackQuery) -> None:
    await query.message.edit_text(
        await _build_dev_settings_text(),
        reply_markup=await _dev_settings_keyboard(),
    )


async def _show_dev_force_join_menu(query: CallbackQuery) -> None:
    await query.message.edit_text(
        await _build_dev_force_join_text(),
        reply_markup=await _dev_force_join_keyboard(),
    )


async def _toggle_dev_setting(
    query: CallbackQuery,
    key: str,
    _label_key: str,
    *,
    default: bool = False,
) -> None:
    await query.answer()
    await settings_repo.toggle_bot_setting(
        key,
        updated_by=query.from_user.id,
        default=default,
    )
    if key == "force_join_enabled":
        await ForcedMembershipService.invalidate_cache()
    await AdminDashboardService.invalidate_cache("general")
    if key == "force_join_enabled":
        await _show_dev_force_join_menu(query)
        return
    await _show_dev_settings_menu(query)


def _format_role_line(entry) -> str:
    name = getattr(entry, "display_name", None) or "-"
    username_raw = getattr(entry, "username", None)
    username = f"@{username_raw}" if username_raw else "-"
    line = t(
        _LANG,
        "list_fmt.role_user_item",
        name=name,
        user_id=getattr(entry, "user_id", 0),
        username=username,
    )
    if hasattr(entry, "admin_title"):
        title = getattr(entry, "admin_title", None) or t(_LANG, "admin_titles.not_set")
        line += "\n" + t(_LANG, "admin_titles.list_line", title=title)
    return line


def _role_list_kb(page: int, total_pages: int, page_prefix: str, return_to: str):
    from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows: list[list[InlineKeyboardButton]] = []
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                t(_LANG, "common.buttons.prev"),
                callback_data=f"{page_prefix}{page - 1}",
            )
        )
    if page + 1 < total_pages:
        nav.append(
            InlineKeyboardButton(
                t(_LANG, "common.buttons.next"),
                callback_data=f"{page_prefix}{page + 1}",
            )
        )
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                t(_LANG, "common.buttons.back"),
                callback_data=f"{CB['WZ_BACK_PREFIX']}{return_to}",
            )
        ]
    )
    rows.append([InlineKeyboardButton(t(_LANG, "common.buttons.home"), callback_data=CB["WZ_HOME"])])
    return InlineKeyboardMarkup(rows)


async def _render_role_list(
    query: CallbackQuery,
    entries: list,
    title_key: str,
    empty_key: str,
    page: int,
    page_prefix: str,
    return_to: str,
) -> None:
    if not entries:
        await query.message.edit_text(
            t(_LANG, empty_key),
            reply_markup=build_done_kb(_LANG, return_to),
        )
        return

    page_size = 10
    total_pages = max(1, (len(entries) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    chunk = entries[page * page_size : (page + 1) * page_size]

    lines = [t(_LANG, title_key), ""]
    lines.extend(_format_role_line(e) for e in chunk)
    lines.append("")
    lines.append(t(_LANG, "list_fmt.page_indicator", page=page + 1, total=total_pages))

    await query.message.edit_text(
        "\n".join(lines),
        reply_markup=_role_list_kb(page, total_pages, page_prefix, return_to),
    )


def _owner_title_list_kb(page: int, total_pages: int, entries: list) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for entry in entries:
        user_id = getattr(entry, "user_id", 0)
        rows.append(
            [
                InlineKeyboardButton(
                    t(_LANG, "admin_titles.apply_telegram_title"),
                    callback_data=f"{CB['DEV_TITLE_APPLY_OWNER_PREFIX']}{user_id}:{page}",
                ),
                InlineKeyboardButton(
                    t(_LANG, "admin_titles.promote_telegram_admin"),
                    callback_data=f"{CB['DEV_TGPROM_OWNER_PREFIX']}{user_id}:{page}",
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    t(_LANG, "admin_titles.set_owner"),
                    callback_data=f"{CB['DEV_TITLE_OWNER_SET_PREFIX']}{user_id}:{page}",
                ),
                InlineKeyboardButton(
                    t(_LANG, "admin_titles.clear_owner"),
                    callback_data=f"{CB['DEV_TITLE_OWNER_CLEAR_PREFIX']}{user_id}:{page}",
                ),
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                t(_LANG, "common.buttons.prev"),
                callback_data=f"{CB['DEV_TITLE_OWNER_LIST_PREFIX']}{page - 1}",
            )
        )
    if page + 1 < total_pages:
        nav.append(
            InlineKeyboardButton(
                t(_LANG, "common.buttons.next"),
                callback_data=f"{CB['DEV_TITLE_OWNER_LIST_PREFIX']}{page + 1}",
            )
        )
    if nav:
        rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton(
                t(_LANG, "admin_titles.back_to_settings"),
                callback_data=CB["DEV_ADMIN_TITLES"],
            )
        ]
    )
    rows.append([InlineKeyboardButton(t(_LANG, "common.buttons.home"), callback_data=CB["WZ_HOME"])])
    return InlineKeyboardMarkup(rows)


async def _render_owner_title_list(query: CallbackQuery, page: int) -> None:
    owners = await user_repo.get_all_owners()
    if not owners:
        await query.message.edit_text(
            t(_LANG, "owner_mgmt.list_empty"),
            reply_markup=build_done_kb(_LANG, TOKEN_DEV_USERS),
        )
        return
    page_size = 10
    total_pages = max(1, (len(owners) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    chunk = owners[page * page_size : (page + 1) * page_size]
    lines = [t(_LANG, "admin_titles.owner_titles"), ""]
    lines.extend(_format_role_line(owner) for owner in chunk)
    lines.append("")
    lines.append(t(_LANG, "admin_titles.note"))
    lines.append(t(_LANG, "list_fmt.page_indicator", page=page + 1, total=total_pages))
    await query.message.edit_text(
        "\n".join(lines),
        reply_markup=_owner_title_list_kb(page, total_pages, chunk),
    )


def _sudo_role_list_kb(
    page: int,
    total_pages: int,
    page_prefix: str,
    return_to: str,
    entries: list,
) -> InlineKeyboardMarkup:
    from pyrogram.types import InlineKeyboardButton

    rows: list[list[InlineKeyboardButton]] = []
    for entry in entries:
        user_id = getattr(entry, "user_id", 0)
        rows.append(
            [
                InlineKeyboardButton(
                    t(_LANG, "reports.detail_btn"),
                    callback_data=f"{CB['DEV_SUDO_DETAIL_PREFIX']}{user_id}:{page}",
                )
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                t(_LANG, "common.buttons.prev"),
                callback_data=f"{page_prefix}{page - 1}",
            )
        )
    if page + 1 < total_pages:
        nav.append(
            InlineKeyboardButton(
                t(_LANG, "common.buttons.next"),
                callback_data=f"{page_prefix}{page + 1}",
            )
        )
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                t(_LANG, "common.buttons.back"),
                callback_data=f"{CB['WZ_BACK_PREFIX']}{return_to}",
            )
        ]
    )
    rows.append([InlineKeyboardButton(t(_LANG, "common.buttons.home"), callback_data=CB["WZ_HOME"])])
    return InlineKeyboardMarkup(rows)


async def _render_sudo_list_page(query: CallbackQuery, page: int) -> None:
    """Render paginated sudo list with per-row Details buttons."""
    sudos = await user_repo.get_all_sudos()
    if not sudos:
        await query.message.edit_text(
            t(_LANG, "sudo_mgmt.list_empty"),
            reply_markup=build_done_kb(_LANG, TOKEN_DEV_USERS),
        )
        return

    page_size = 10
    total_pages = max(1, (len(sudos) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    chunk = sudos[page * page_size : (page + 1) * page_size]

    lines = [t(_LANG, "sudo_mgmt.list_title"), ""]
    lines.extend(_format_role_line(e) for e in chunk)
    lines.append("")
    lines.append(t(_LANG, "list_fmt.page_indicator", page=page + 1, total=total_pages))

    await query.message.edit_text(
        "\n".join(lines),
        reply_markup=_sudo_role_list_kb(
            page,
            total_pages,
            CB["PAGE_DEV_SUDOS"],
            TOKEN_DEV_USERS,
            chunk,
        ),
    )


def _parse_sudo_detail_payload(data: str, prefix: str) -> tuple[int, int] | None:
    if not data.startswith(prefix):
        return None
    raw = data[len(prefix):]
    parts = raw.split(":")
    if len(parts) != 2:
        return None
    user_id = parse_user_id(parts[0])
    if user_id is None:
        return None
    try:
        page = max(0, int(parts[1]))
    except ValueError:
        return None
    return user_id, page


def _sudo_permission_label(enabled: bool) -> str:
    return t(
        _LANG,
        "sudo_permissions.state_enabled" if enabled else "sudo_permissions.state_disabled",
    )


def _format_admin_title(value: str | None) -> str:
    title = (value or "").strip()
    return title or t(_LANG, "admin_titles.not_set")


def normalize_admin_title(raw: str | None) -> tuple[str | None, str | None]:
    """Normalize stored admin title input; return (title, error_key)."""
    title = (raw or "").strip()
    if not title:
        return None, "admin_titles.invalid"
    if len(title) > _ADMIN_TITLE_MAX_LEN:
        return None, "admin_titles.too_long"
    if any(ord(ch) < 32 or ch in "\r\n\t" for ch in title):
        return None, "admin_titles.invalid"
    if _ADMIN_TITLE_URL_RE.search(title):
        return None, "admin_titles.invalid"
    return title, None


async def _build_admin_title_settings_text() -> str:
    developer_title = await settings_repo.get_developer_admin_title()
    return "\n".join(
        [
            t(_LANG, "admin_titles.settings_title"),
            "",
            t(
                _LANG,
                "admin_titles.detail_line",
                label=t(_LANG, "admin_titles.developer_admin_title"),
                title=_format_admin_title(developer_title),
            ),
            "",
            t(_LANG, "admin_titles.note"),
        ]
    )


async def _show_admin_title_settings(query: CallbackQuery) -> None:
    await query.message.edit_text(
        await _build_admin_title_settings_text(),
        reply_markup=KeyboardFactory.dev_admin_titles(_LANG),
    )


def _parse_admin_title_dev_clear(data: str, prefix: str) -> tuple[int, int] | None:
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix) :].split(":")
    if len(parts) != 2:
        return None
    try:
        actor_id = int(parts[0])
        issued_at = int(parts[1])
    except ValueError:
        return None
    if actor_id <= 0 or issued_at <= 0:
        return None
    return actor_id, issued_at


def _parse_admin_title_role_target(data: str, prefix: str) -> tuple[int, int] | None:
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix) :].split(":")
    if len(parts) != 2:
        return None
    target_id = parse_user_id(parts[0])
    if target_id is None:
        return None
    try:
        page = max(0, int(parts[1]))
    except ValueError:
        return None
    return target_id, page


def _parse_admin_title_role_clear(data: str, prefix: str) -> tuple[int, int, int, int] | None:
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix) :].split(":")
    if len(parts) != 4:
        return None
    target_id = parse_user_id(parts[0])
    if target_id is None:
        return None
    try:
        page = max(0, int(parts[1]))
        actor_id = int(parts[2])
        issued_at = int(parts[3])
    except ValueError:
        return None
    if actor_id <= 0 or issued_at <= 0:
        return None
    return target_id, page, actor_id, issued_at


def _parse_admin_title_chat_id(raw: str | None) -> int | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        chat_id = int(text)
    except ValueError:
        return None
    if chat_id == 0:
        return None
    return chat_id


def _parse_admin_title_apply_confirm(data: str, prefix: str) -> tuple[str, int, int] | None:
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix) :].split(":")
    if len(parts) != 3:
        return None
    token, actor_raw, issued_raw = parts
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,24}", token):
        return None
    try:
        actor_id = int(actor_raw)
        issued_at = int(issued_raw)
    except ValueError:
        return None
    if actor_id <= 0 or issued_at <= 0:
        return None
    return token, actor_id, issued_at


def _admin_title_apply_return_callback(kind: str, target_id: int, page: int) -> str:
    if kind == "s":
        return f"{CB['DEV_SUDO_DETAIL_PREFIX']}{target_id}:{page}"
    if kind == "o":
        return f"{CB['DEV_TITLE_OWNER_LIST_PREFIX']}{page}"
    return CB["DEV_ADMIN_TITLES"]


def _admin_title_apply_target_id(kind: str, target_id: int) -> int:
    """Resolve Telegram user id for admin-title apply (kind d uses passed acting dev)."""
    return target_id


async def _get_admin_title_for_apply(kind: str, target_id: int) -> tuple[str | None, str | None]:
    if kind == "d":
        title = await settings_repo.get_developer_admin_title()
    elif kind == "o":
        owner = await user_repo.get_owner(target_id)
        if owner is None:
            return None, "owner_mgmt.not_found"
        title = getattr(owner, "admin_title", None)
    elif kind == "s":
        sudo = await user_repo.get_sudo(target_id)
        if sudo is None:
            return None, "sudo_mgmt.not_found"
        title = getattr(sudo, "admin_title", None)
    else:
        return None, "common.errors.invalid_callback"

    normalized, error_key = normalize_admin_title(title)
    if error_key is not None:
        return None, "admin_titles.apply_title_not_set"
    return normalized, None


async def _store_admin_title_apply_context(kind: str, target_id: int, chat_id: int, page: int) -> str:
    token = secrets.token_urlsafe(8).rstrip("=")
    r = await get_redis()
    await r.set(
        f"{_TITLE_APPLY_PENDING_PREFIX}{token}",
        f"{kind}:{target_id}:{chat_id}:{page}",
        ex=_DEV_CONFIRM_TTL_SECONDS,
    )
    return token


async def _get_admin_title_apply_context(token: str) -> tuple[str, int, int, int] | None:
    r = await get_redis()
    raw = await r.get(f"{_TITLE_APPLY_PENDING_PREFIX}{token}")
    if not raw:
        return None
    parts = str(raw).split(":")
    if len(parts) != 4:
        return None
    kind = parts[0]
    if kind not in {"d", "o", "s"}:
        return None
    try:
        target_id = int(parts[1])
        chat_id = int(parts[2])
        page = max(0, int(parts[3]))
    except ValueError:
        return None
    return kind, target_id, chat_id, page


async def _delete_admin_title_apply_context(token: str) -> None:
    r = await get_redis()
    await r.delete(f"{_TITLE_APPLY_PENDING_PREFIX}{token}")


def _admin_title_apply_confirm_kb(token: str, actor_id: int, issued_at: int, back_callback: str) -> InlineKeyboardMarkup:
    payload = f"{token}:{actor_id}:{issued_at}"
    return InlineKeyboardMarkup(
        [
            [
                compatible_inline_button(
                    t(_LANG, "common.buttons.confirm"),
                    callback_data=f"{CB['DEV_TITLE_APPLY_DO_PREFIX']}{payload}",
                ),
                compatible_inline_button(
                    t(_LANG, "common.buttons.cancel_inline"),
                    callback_data=f"{CB['DEV_TITLE_APPLY_NO_PREFIX']}{payload}",
                ),
            ],
            [InlineKeyboardButton(t(_LANG, "common.buttons.back"), callback_data=back_callback)],
        ]
    )


async def _start_admin_title_apply(
    client: Client,
    query: CallbackQuery,
    kind: str,
    target_id: int,
    page: int,
) -> None:
    title, error_key = await _get_admin_title_for_apply(kind, target_id)
    back_callback = _admin_title_apply_return_callback(kind, target_id, page)
    if error_key is not None or title is None:
        await query.answer(t(_LANG, error_key or "admin_titles.apply_title_not_set"), show_alert=True)
        return

    await query.answer()
    resp, chat_id = await _ask_value(
        client,
        query.message.chat.id,
        "admin_titles.apply_enter_chat_id",
        lambda message: _parse_admin_title_chat_id(
            getattr(message, "text", None)
        ),
        t(_LANG, "admin_titles.apply_invalid_chat_id"),
        user_id=query.from_user.id,
        return_to=TOKEN_DEV_USERS,
    )
    if resp.message is None:
        return

    target_user_id = _admin_title_apply_target_id(kind, target_id)
    preflight = await preflight_admin_title_apply(client, chat_id, target_user_id)
    if not preflight.ok:
        await _send_admin_title_result(
            client,
            query.message.chat.id,
            t(_LANG, preflight.message_key),
            back_callback,
        )
        return

    issued_at = int(time.time())
    token = await _store_admin_title_apply_context(kind, target_id, chat_id, page)
    await deliver_ask_outcome(
        client,
        query.message.chat.id,
        query.from_user.id,
        t(
            _LANG,
            "admin_titles.apply_confirm",
            user_id=target_user_id,
            chat_id=chat_id,
            title=title,
        ),
        _admin_title_apply_confirm_kb(
            token,
            query.from_user.id,
            issued_at,
            back_callback,
        ),
    )


async def _validate_promotion_target(kind: str, target_id: int) -> str | None:
    """Return localized error key when the promotion target is missing."""
    if kind == "d":
        return None
    if kind == "o":
        if await user_repo.get_owner(target_id) is None:
            return "owner_mgmt.not_found"
        return None
    if kind == "s":
        if await user_repo.get_sudo(target_id) is None:
            return "sudo_mgmt.not_found"
        return None
    return "common.errors.invalid_callback"


async def _get_optional_stored_admin_title(kind: str, target_id: int) -> tuple[str | None, str | None]:
    """Return optional normalized stored title; blocking errors only for missing targets."""
    if kind == "d":
        title = await settings_repo.get_developer_admin_title()
    elif kind == "o":
        owner = await user_repo.get_owner(target_id)
        if owner is None:
            return None, "owner_mgmt.not_found"
        title = getattr(owner, "admin_title", None)
    elif kind == "s":
        sudo = await user_repo.get_sudo(target_id)
        if sudo is None:
            return None, "sudo_mgmt.not_found"
        title = getattr(sudo, "admin_title", None)
    else:
        return None, "common.errors.invalid_callback"

    normalized, error_key = normalize_admin_title(title)
    if error_key is not None:
        return None, None
    return normalized, None


def _tg_prom_return_callback(kind: str, target_id: int, page: int) -> str:
    return _admin_title_apply_return_callback(kind, target_id, page)


async def _store_tg_prom_context(kind: str, target_id: int, chat_id: int, page: int) -> str:
    token = secrets.token_urlsafe(8).rstrip("=")
    r = await get_redis()
    await r.set(
        f"{_TGPROM_PENDING_PREFIX}{token}",
        f"{kind}:{target_id}:{chat_id}:{page}",
        ex=_DEV_CONFIRM_TTL_SECONDS,
    )
    return token


async def _get_tg_prom_context(token: str) -> tuple[str, int, int, int] | None:
    r = await get_redis()
    raw = await r.get(f"{_TGPROM_PENDING_PREFIX}{token}")
    if not raw:
        return None
    parts = str(raw).split(":")
    if len(parts) != 4:
        return None
    kind = parts[0]
    if kind not in {"d", "o", "s"}:
        return None
    try:
        target_id = int(parts[1])
        chat_id = int(parts[2])
        page = max(0, int(parts[3]))
    except ValueError:
        return None
    return kind, target_id, chat_id, page


async def _delete_tg_prom_context(token: str) -> None:
    r = await get_redis()
    await r.delete(f"{_TGPROM_PENDING_PREFIX}{token}")


def _tg_prom_confirm_kb(token: str, actor_id: int, issued_at: int, back_callback: str) -> InlineKeyboardMarkup:
    payload = f"{token}:{actor_id}:{issued_at}"
    return InlineKeyboardMarkup(
        [
            [
                compatible_inline_button(
                    t(_LANG, "common.buttons.confirm"),
                    callback_data=f"{CB['DEV_TGPROM_DO_PREFIX']}{payload}",
                ),
                compatible_inline_button(
                    t(_LANG, "common.buttons.cancel_inline"),
                    callback_data=f"{CB['DEV_TGPROM_NO_PREFIX']}{payload}",
                ),
            ],
            [InlineKeyboardButton(t(_LANG, "common.buttons.back"), callback_data=back_callback)],
        ]
    )


async def _start_manual_telegram_promotion(
    client: Client,
    query: CallbackQuery,
    kind: str,
    target_id: int,
    page: int,
) -> None:
    target_error = await _validate_promotion_target(kind, target_id)
    back_callback = _tg_prom_return_callback(kind, target_id, page)
    if target_error is not None:
        await query.answer(t(_LANG, target_error), show_alert=True)
        return

    await query.answer()
    resp, chat_id = await _ask_value(
        client,
        query.message.chat.id,
        "admin_titles.prom_enter_chat_id",
        lambda message: _parse_admin_title_chat_id(
            getattr(message, "text", None)
        ),
        t(_LANG, "admin_titles.prom_invalid_chat_id"),
        user_id=query.from_user.id,
        return_to=TOKEN_DEV_USERS,
    )
    if resp.message is None:
        return

    target_user_id = _admin_title_apply_target_id(kind, target_id)
    preflight = await preflight_manual_telegram_promotion(client, chat_id, target_user_id)
    if not preflight.ok:
        await _send_admin_title_result(
            client,
            query.message.chat.id,
            t(_LANG, preflight.message_key),
            back_callback,
        )
        return

    stored_title, title_error = await _get_optional_stored_admin_title(kind, target_id)
    if title_error is not None:
        await _send_admin_title_result(
            client,
            query.message.chat.id,
            t(_LANG, title_error),
            back_callback,
        )
        return

    issued_at = int(time.time())
    token = await _store_tg_prom_context(kind, target_id, chat_id, page)
    title_line = (
        t(_LANG, "admin_titles.prom_confirm_title", title=stored_title)
        if stored_title
        else t(_LANG, "admin_titles.prom_confirm_no_title")
    )
    await deliver_ask_outcome(
        client,
        query.message.chat.id,
        query.from_user.id,
        t(
            _LANG,
            "admin_titles.prom_confirm",
            user_id=target_user_id,
            chat_id=chat_id,
            privileges_summary=t(_LANG, "admin_titles.prom_minimal_privileges"),
            title_line=title_line,
        ),
        _tg_prom_confirm_kb(
            token,
            query.from_user.id,
            issued_at,
            back_callback,
        ),
    )


async def _execute_manual_telegram_promotion(
    client: Client,
    query: CallbackQuery,
    kind: str,
    target_id: int,
    chat_id: int,
    page: int,
) -> None:
    back_callback = _tg_prom_return_callback(kind, target_id, page)
    target_error = await _validate_promotion_target(kind, target_id)
    if target_error is not None:
        await query.answer(t(_LANG, target_error), show_alert=True)
        return

    target_user_id = _admin_title_apply_target_id(kind, target_id)
    preflight = await preflight_manual_telegram_promotion(client, chat_id, target_user_id)
    if not preflight.ok:
        await query.message.edit_text(
            t(_LANG, preflight.message_key),
            reply_markup=_admin_title_done_kb(back_callback),
        )
        return

    try:
        await promote_telegram_admin(client, chat_id, target_user_id)
    except Exception as exc:
        exc_name = type(exc).__name__
        if "FloodWait" in exc_name:
            wait = getattr(exc, "value", 0)
            await query.message.edit_text(
                t(_LANG, "admin_titles.prom_failed_flood", wait=wait),
                reply_markup=_admin_title_done_kb(back_callback),
            )
            return
        await query.message.edit_text(
            t(_LANG, "admin_titles.prom_failed"),
            reply_markup=_admin_title_done_kb(back_callback),
        )
        return

    stored_title, _title_error = await _get_optional_stored_admin_title(kind, target_id)
    if stored_title:
        try:
            await apply_admin_title(client, chat_id, target_user_id, stored_title)
            success_text = t(_LANG, "admin_titles.prom_success_with_title")
        except Exception:
            success_text = t(_LANG, "admin_titles.prom_success_title_failed")
    else:
        success_text = t(_LANG, "admin_titles.prom_success")

    await query.answer(success_text, show_alert=True)
    await query.message.edit_text(
        success_text,
        reply_markup=_admin_title_done_kb(back_callback),
    )


def _admin_title_clear_confirm_kb(kind: str, payload: str, back_callback: str) -> InlineKeyboardMarkup:
    if kind == "dev":
        yes = f"{CB['DEV_TITLE_DEV_CLEAR_DO_PREFIX']}{payload}"
        no = f"{CB['DEV_TITLE_DEV_CLEAR_NO_PREFIX']}{payload}"
    elif kind == "owner":
        yes = f"{CB['DEV_TITLE_OWNER_CLEAR_DO_PREFIX']}{payload}"
        no = f"{CB['DEV_TITLE_OWNER_CLEAR_NO_PREFIX']}{payload}"
    else:
        yes = f"{CB['DEV_TITLE_SUDO_CLEAR_DO_PREFIX']}{payload}"
        no = f"{CB['DEV_TITLE_SUDO_CLEAR_NO_PREFIX']}{payload}"
    return InlineKeyboardMarkup(
        [
            [
                compatible_inline_button(
                    t(_LANG, "common.buttons.confirm"),
                    callback_data=yes,
                ),
                compatible_inline_button(
                    t(_LANG, "common.buttons.cancel_inline"),
                    callback_data=no,
                ),
            ],
            [InlineKeyboardButton(t(_LANG, "common.buttons.back"), callback_data=back_callback)],
        ]
    )


def _admin_title_done_kb(back_callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(t(_LANG, "common.buttons.back"), callback_data=back_callback)],
            [InlineKeyboardButton(t(_LANG, "common.buttons.home"), callback_data=CB["WZ_HOME"])],
        ]
    )


async def _send_admin_title_result(
    client: Client,
    chat_id: int,
    text: str,
    back_callback: str,
) -> None:
    await deliver_ask_outcome(
        client,
        chat_id,
        chat_id,
        text,
        _admin_title_done_kb(back_callback),
    )


async def _format_sudo_detail_text(sudo) -> str:
    perms = user_repo.sudo_permissions_from_row(sudo)
    developer_admin_title = await settings_repo.get_developer_admin_title()
    added_at = (
        sudo.added_at.strftime("%Y-%m-%d %H:%M")
        if getattr(sudo, "added_at", None) is not None
        else t(_LANG, "reports.value_unavailable")
    )
    link_state = (
        t(_LANG, "sudo_permissions.link_configured")
        if getattr(sudo, "sudo_link", None)
        else t(_LANG, "sudo_permissions.link_not_set")
    )
    active_state = _sudo_permission_label(bool(getattr(sudo, "is_active", True)))

    lines = [
        t(_LANG, "sudo_permissions.detail_title"),
        "",
        t(
            _LANG,
            "sudo_permissions.detail_body",
            user_id=sudo.user_id,
            username=sudo.username or t(_LANG, "reports.value_unavailable"),
            display_name=sudo.display_name or t(_LANG, "reports.value_unavailable"),
            added_by=sudo.added_by if sudo.added_by is not None else t(_LANG, "reports.value_unavailable"),
            added_at=added_at,
            is_active=active_state,
            total_installs=int(getattr(sudo, "total_installs", 0) or 0),
            link_state=link_state,
        ),
        "",
        t(_LANG, "admin_titles.section_title"),
        t(
            _LANG,
            "admin_titles.detail_line",
            label=t(_LANG, "admin_titles.developer_admin_title"),
            title=_format_admin_title(developer_admin_title),
        ),
        t(
            _LANG,
            "admin_titles.detail_line",
            label=t(_LANG, "admin_titles.sudo_admin_title"),
            title=_format_admin_title(getattr(sudo, "admin_title", None)),
        ),
        t(_LANG, "admin_titles.note"),
        "",
        t(_LANG, "sudo_permissions.matrix_title"),
        t(
            _LANG,
            "sudo_permissions.perm_line",
            label=t(_LANG, "sudo_permissions.perm_groups"),
            state=_sudo_permission_label(perms["can_manage_groups"]),
        ),
        t(
            _LANG,
            "sudo_permissions.perm_line",
            label=t(_LANG, "sudo_permissions.perm_channels"),
            state=_sudo_permission_label(perms["can_manage_channels"]),
        ),
        t(
            _LANG,
            "sudo_permissions.perm_line",
            label=t(_LANG, "sudo_permissions.perm_credit"),
            state=_sudo_permission_label(perms["can_manage_credit"]),
        ),
        t(
            _LANG,
            "sudo_permissions.perm_line",
            label=t(_LANG, "sudo_permissions.perm_remove_bot"),
            state=_sudo_permission_label(perms["can_remove_bot"]),
        ),
        t(
            _LANG,
            "sudo_permissions.perm_line",
            label=t(_LANG, "sudo_permissions.perm_chat_settings"),
            state=_sudo_permission_label(perms["can_manage_chat_settings"]),
        ),
        t(
            _LANG,
            "sudo_permissions.perm_line",
            label=t(_LANG, "sudo_permissions.perm_auto_admin"),
            state=_sudo_permission_label(perms["auto_admin_bypass"]),
        ),
        "",
        t(_LANG, "sudo_permissions.perm_auto_admin_note"),
    ]
    return "\n".join(lines)


def _sudo_perm_callback_key(field: str) -> str | None:
    from app.repositories.user_repo import SUDO_PERMISSION_FIELD_TO_CALLBACK_KEY

    return SUDO_PERMISSION_FIELD_TO_CALLBACK_KEY.get(field)


def _sudo_perm_field_label(field: str) -> str:
    from app.repositories.user_repo import SUDO_PERMISSION_FIELD_LABEL_KEYS

    label_key = SUDO_PERMISSION_FIELD_LABEL_KEYS.get(field, "sudo_permissions.perm_groups")
    return t(_LANG, label_key)


def _parse_sudo_perm_toggle_payload(data: str, prefix: str) -> tuple[str, int, int] | None:
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix) :].split(":")
    if len(parts) != 3:
        return None
    key, target_raw, page_raw = parts
    from app.repositories.user_repo import resolve_sudo_permission_field

    if resolve_sudo_permission_field(key) is None:
        return None
    target_uid = parse_user_id(target_raw)
    if target_uid is None:
        return None
    try:
        page = max(0, int(page_raw))
    except ValueError:
        return None
    return key, target_uid, page


def _parse_sudo_perm_confirm_bound(data: str, prefix: str) -> tuple[str, int, int, int, int] | None:
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix) :].split(":")
    if len(parts) != 5:
        return None
    key, target_raw, page_raw, actor_raw, issued_raw = parts
    from app.repositories.user_repo import resolve_sudo_permission_field

    if resolve_sudo_permission_field(key) is None:
        return None
    target_uid = parse_user_id(target_raw)
    if target_uid is None:
        return None
    try:
        page = max(0, int(page_raw))
        actor_id = int(actor_raw)
        issued_at = int(issued_raw)
    except ValueError:
        return None
    if actor_id <= 0 or issued_at <= 0:
        return None
    return key, target_uid, page, actor_id, issued_at


def _sudo_perm_confirm_kb(
    key: str,
    target_uid: int,
    page: int,
    actor_id: int,
    issued_at: int,
) -> InlineKeyboardMarkup:
    payload = f"{key}:{target_uid}:{page}:{actor_id}:{issued_at}"
    return InlineKeyboardMarkup(
        [
            [
                compatible_inline_button(
                    t(_LANG, "common.buttons.confirm"),
                    callback_data=f"{CB['DEV_SUDO_PERM_DO_PREFIX']}{payload}",
                ),
                compatible_inline_button(
                    t(_LANG, "common.buttons.cancel_inline"),
                    callback_data=f"{CB['DEV_SUDO_PERM_NO_PREFIX']}{payload}",
                ),
            ],
            [
                InlineKeyboardButton(
                    t(_LANG, "sudo_permissions.back_to_detail"),
                    callback_data=f"{CB['DEV_SUDO_DETAIL_PREFIX']}{target_uid}:{page}",
                ),
            ],
        ]
    )


def _sudo_detail_kb(sudo, page: int) -> InlineKeyboardMarkup:
    from app.repositories.user_repo import SUDO_PERMISSION_FIELD_NAMES

    perms = user_repo.sudo_permissions_from_row(sudo)
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                t(_LANG, "admin_titles.apply_telegram_title"),
                callback_data=f"{CB['DEV_TITLE_APPLY_SUDO_PREFIX']}{sudo.user_id}:{page}",
            ),
            InlineKeyboardButton(
                t(_LANG, "admin_titles.promote_telegram_admin"),
                callback_data=f"{CB['DEV_TGPROM_SUDO_PREFIX']}{sudo.user_id}:{page}",
            ),
        ],
        [
            InlineKeyboardButton(
                t(_LANG, "admin_titles.set_sudo"),
                callback_data=f"{CB['DEV_TITLE_SUDO_SET_PREFIX']}{sudo.user_id}:{page}",
            ),
            InlineKeyboardButton(
                t(_LANG, "admin_titles.clear_sudo"),
                callback_data=f"{CB['DEV_TITLE_SUDO_CLEAR_PREFIX']}{sudo.user_id}:{page}",
            ),
        ],
    ]
    for field in SUDO_PERMISSION_FIELD_NAMES:
        key = _sudo_perm_callback_key(field)
        if key is None:
            continue
        enabled = perms[field]
        label = _sudo_perm_field_label(field)
        btn_key = (
            "sudo_permissions.toggle_disable_btn"
            if enabled
            else "sudo_permissions.toggle_enable_btn"
        )
        button = InlineKeyboardButton(
            t(_LANG, btn_key, label=label),
            callback_data=f"{CB['DEV_SUDO_PERM_TOGGLE_PREFIX']}{key}:{sudo.user_id}:{page}",
        )
        mark_toggle_state(button, enabled)
        rows.append([button])
    rows.append(
        [
            InlineKeyboardButton(
                t(_LANG, "sudo_permissions.back_to_list"),
                callback_data=f"{CB['DEV_SUDO_LIST_BACK_PREFIX']}{page}",
            ),
        ]
    )
    rows.append([InlineKeyboardButton(t(_LANG, "common.buttons.home"), callback_data=CB["WZ_HOME"])])
    return InlineKeyboardMarkup(rows)


async def _render_sudo_detail(query: CallbackQuery, user_id: int, page: int) -> None:
    sudo = await user_repo.get_sudo_record(user_id)
    if sudo is None:
        await query.message.edit_text(
            t(_LANG, "sudo_permissions.not_found", user_id=user_id),
            reply_markup=build_done_kb(_LANG, TOKEN_DEV_USERS),
        )
        return

    await query.message.edit_text(
        await _format_sudo_detail_text(sudo),
        reply_markup=_sudo_detail_kb(sudo, page),
    )


_REPORT_PAGE_SIZE = 3
_SOURCE_GROUPS = "g"
_SOURCE_CHANNELS = "c"
_SOURCE_NO_CREDIT = "n"
_SOURCE_RENEWAL = "r"
_SOURCE_MANUAL = "m"
_SOURCE_UNLIMITED = "u"
_SOURCE_CALL_SECURITY = "s"
_SOURCE_PLAYBACK = "p"
_SOURCE_TRIAL = "t"
_SOURCE_MUSIC = "a"
_SOURCE_VIDEO = "v"
_SOURCE_INACTIVE_GROUPS = "i"
_SOURCE_INACTIVE_CHANNELS = "x"
_CTYPE_GROUP = "g"
_CTYPE_CHANNEL = "c"

# Sources routed through the compact extended pagination prefix (pg:dx:<src>:<page>).
_EXTENDED_LIST_SOURCES = (
    _SOURCE_UNLIMITED,
    _SOURCE_CALL_SECURITY,
    _SOURCE_PLAYBACK,
    _SOURCE_TRIAL,
    _SOURCE_MUSIC,
    _SOURCE_VIDEO,
    _SOURCE_INACTIVE_GROUPS,
    _SOURCE_INACTIVE_CHANNELS,
)

_SOURCE_TITLE_KEYS = {
    _SOURCE_GROUPS: "reports.groups_title",
    _SOURCE_CHANNELS: "reports.channels_title",
    _SOURCE_NO_CREDIT: "reports.no_credit_title",
    _SOURCE_RENEWAL: "reports.renewal_title",
    _SOURCE_UNLIMITED: "reports.unlimited_title",
    _SOURCE_CALL_SECURITY: "reports.call_security_title",
    _SOURCE_PLAYBACK: "reports.playback_title",
    _SOURCE_TRIAL: "reports.trial_title",
    _SOURCE_MUSIC: "reports.music_title",
    _SOURCE_VIDEO: "reports.video_title",
    _SOURCE_INACTIVE_GROUPS: "reports.inactive_groups_title",
    _SOURCE_INACTIVE_CHANNELS: "reports.inactive_channels_title",
}

_SOURCE_EMPTY_KEYS = {
    _SOURCE_NO_CREDIT: "reports.no_credit_empty",
    _SOURCE_RENEWAL: "reports.renewal_empty",
    _SOURCE_UNLIMITED: "reports.unlimited_empty",
    _SOURCE_CALL_SECURITY: "reports.call_security_empty",
    _SOURCE_PLAYBACK: "reports.playback_empty",
    _SOURCE_TRIAL: "reports.trial_empty",
    _SOURCE_MUSIC: "reports.music_empty",
    _SOURCE_VIDEO: "reports.video_empty",
    _SOURCE_INACTIVE_GROUPS: "reports.inactive_groups_empty",
    _SOURCE_INACTIVE_CHANNELS: "reports.inactive_channels_empty",
}


def _chat_type_to_char(chat_type: str) -> str:
    return _CTYPE_GROUP if chat_type == "group" else _CTYPE_CHANNEL


def _char_to_chat_type(char: str) -> str | None:
    if char == _CTYPE_GROUP:
        return "group"
    if char == _CTYPE_CHANNEL:
        return "channel"
    return None


def _valid_list_source(source: str) -> bool:
    return (
        source in (_SOURCE_GROUPS, _SOURCE_CHANNELS, _SOURCE_NO_CREDIT, _SOURCE_RENEWAL, _SOURCE_MANUAL)
        or source in _EXTENDED_LIST_SOURCES
    )


def _parse_list_row_payload(data: str, prefix: str) -> tuple[str, str, int, int] | None:
    """Parse list row callback payload into source, chat_type, chat_id, page."""
    if not data.startswith(prefix):
        return None
    raw = data[len(prefix):]
    parts = raw.split(":")
    if len(parts) != 4:
        return None
    source, ctype_char, chat_id_raw, page_raw = parts
    if not _valid_list_source(source):
        return None
    chat_type = _char_to_chat_type(ctype_char)
    if chat_type is None:
        return None
    chat_id = parse_user_id(chat_id_raw)
    if chat_id is None:
        return None
    try:
        page = max(0, int(page_raw))
    except ValueError:
        return None
    return source, chat_type, chat_id, page


def _list_row_payload(source: str, row: admin_report_repo.ChatInstallRow, page: int) -> str:
    return f"{source}:{_chat_type_to_char(row.chat_type)}:{row.chat_id}:{page}"


def _resolve_open_link(row: admin_report_repo.ChatInstallRow) -> str | None:
    """Return a safe Telegram URL for URL buttons, or None when unavailable."""
    link = (row.invite_link or "").strip()
    if not link:
        return None
    if link.startswith("http://t.me/") or link.startswith("https://t.me/"):
        return link.replace("http://", "https://", 1)
    if link.startswith("t.me/"):
        return f"https://{link}"
    return None


def _page_prefix_for_source(source: str) -> str:
    if source == _SOURCE_GROUPS:
        return CB["PAGE_DEV_GROUPS"]
    if source == _SOURCE_CHANNELS:
        return CB["PAGE_DEV_CHANNELS"]
    if source == _SOURCE_NO_CREDIT:
        return CB["PAGE_DEV_NO_CREDIT"]
    if source in _EXTENDED_LIST_SOURCES:
        return f"{CB['PAGE_DEV_LISTX_PREFIX']}{source}:"
    return CB["PAGE_DEV_RENEWAL"]


def _title_key_for_source(source: str) -> str:
    return _SOURCE_TITLE_KEYS.get(source, "reports.renewal_title")


async def _fetch_report_page(source: str, page: int) -> tuple[list[admin_report_repo.ChatInstallRow], int]:
    if source == _SOURCE_GROUPS:
        return await admin_report_repo.get_groups_page(page, page_size=_REPORT_PAGE_SIZE)
    if source == _SOURCE_CHANNELS:
        return await admin_report_repo.get_channels_page(page, page_size=_REPORT_PAGE_SIZE)
    if source == _SOURCE_NO_CREDIT:
        return await admin_report_repo.get_no_credit_page(page, page_size=_REPORT_PAGE_SIZE)
    if source == _SOURCE_UNLIMITED:
        return await admin_report_repo.get_unlimited_groups_page(page, page_size=_REPORT_PAGE_SIZE)
    if source == _SOURCE_CALL_SECURITY:
        return await admin_report_repo.get_call_security_groups_page(page, page_size=_REPORT_PAGE_SIZE)
    if source == _SOURCE_PLAYBACK:
        return await admin_report_repo.get_playback_groups_page(page, page_size=_REPORT_PAGE_SIZE)
    if source == _SOURCE_TRIAL:
        return await admin_report_repo.get_trial_groups_page(page, page_size=_REPORT_PAGE_SIZE)
    if source == _SOURCE_MUSIC:
        return await admin_report_repo.get_media_mode_groups_page(
            page, page_size=_REPORT_PAGE_SIZE, media="music"
        )
    if source == _SOURCE_VIDEO:
        return await admin_report_repo.get_media_mode_groups_page(
            page, page_size=_REPORT_PAGE_SIZE, media="video"
        )
    if source == _SOURCE_INACTIVE_GROUPS:
        return await admin_report_repo.get_inactive_groups_page(page, page_size=_REPORT_PAGE_SIZE)
    if source == _SOURCE_INACTIVE_CHANNELS:
        return await admin_report_repo.get_inactive_channels_page(page, page_size=_REPORT_PAGE_SIZE)
    return await admin_report_repo.get_renewal_page(page, page_size=_REPORT_PAGE_SIZE, hours=24)


def _format_time_left(row: admin_report_repo.ChatInstallRow) -> str:
    if row.expire_at is not None:
        delta = row.expire_at - datetime.now(timezone.utc)
        hours = max(0, int(delta.total_seconds() // 3600))
    else:
        hours = max(0, int(row.credit_days * 24))
    return t(_LANG, "reports.time_left_hours", hours=hours)


def _format_report_line(row: admin_report_repo.ChatInstallRow, source: str, index: int) -> str:
    link_status = (
        t(_LANG, "reports.list_link_available")
        if _resolve_open_link(row)
        else t(_LANG, "reports.list_link_missing")
    )
    installed_by = (
        str(row.installed_by)
        if row.installed_by is not None
        else t(_LANG, "reports.value_unavailable")
    )
    status = (
        label(_LANG, "install_status", row.status)
        if row.status
        else t(_LANG, "reports.value_unavailable")
    )
    line = t(
        _LANG,
        "reports.list_item_compact",
        index=index,
        title=row.title or str(row.chat_id),
        chat_id=row.chat_id,
        credit_days=row.credit_days,
        link_status=link_status,
        installed_by=installed_by,
        status=status,
    )
    if row.expire_at is not None:
        line += "\n" + t(
            _LANG,
            "reports.list_item_expire_at",
            expire_at=row.expire_at.strftime("%Y-%m-%d %H:%M"),
        )
    if source == _SOURCE_RENEWAL:
        line += "\n" + t(
            _LANG,
            "reports.list_item_time_left",
            time_left=_format_time_left(row),
        )
    return line


def _report_page_kb(
    rows: list[admin_report_repo.ChatInstallRow],
    page: int,
    total_pages: int,
    source: str,
) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []

    for row in rows:
        payload = _list_row_payload(source, row, page)
        action_row: list[InlineKeyboardButton] = [
            InlineKeyboardButton(
                t(_LANG, "reports.action_details"),
                callback_data=f"{CB['DEV_LIST_DETAIL_PREFIX']}{payload}",
            ),
        ]
        open_url = _resolve_open_link(row)
        if open_url:
            action_row.append(
                InlineKeyboardButton(
                    t(_LANG, "reports.action_link"),
                    url=open_url,
                )
            )
        buttons.append(action_row)
        # Telegram RTL clients render this row visually right-to-left.
        buttons.append(
            [
                InlineKeyboardButton(
                    t(_LANG, "reports.action_deduct"),
                    callback_data=f"{CB['DEV_LIST_CREDIT_DEC_PREFIX']}{payload}",
                ),
                InlineKeyboardButton(
                    t(_LANG, "reports.action_leave"),
                    callback_data=f"{CB['DEV_LEAVE_CONFIRM_PREFIX']}{row.chat_id}:{source}:{page}",
                ),
                InlineKeyboardButton(
                    t(_LANG, "reports.action_charge"),
                    callback_data=f"{CB['DEV_LIST_CREDIT_INC_PREFIX']}{payload}",
                ),
            ]
        )

    nav: list[InlineKeyboardButton] = []
    page_prefix = _page_prefix_for_source(source)
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                t(_LANG, "reports.pagination_prev"),
                callback_data=f"{page_prefix}{page - 1}",
            )
        )
    if page + 1 < total_pages:
        nav.append(
            InlineKeyboardButton(
                t(_LANG, "reports.pagination_next"),
                callback_data=f"{page_prefix}{page + 1}",
            )
        )
    if nav:
        buttons.append(nav)

    buttons.append(
        [
            InlineKeyboardButton(
                t(_LANG, "reports.nav_back"),
                callback_data=f"{CB['WZ_BACK_PREFIX']}{TOKEN_DEV_LISTS}",
            )
        ]
    )
    buttons.append([InlineKeyboardButton(t(_LANG, "common.buttons.home"), callback_data=CB["WZ_HOME"])])
    return InlineKeyboardMarkup(buttons)


def _detail_kb(
    row: admin_report_repo.ChatInstallRow,
    source: str,
    page: int,
) -> InlineKeyboardMarkup:
    payload = _list_row_payload(source, row, page)
    buttons: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                t(_LANG, "reports.action_deduct"),
                callback_data=f"{CB['DEV_LIST_CREDIT_DEC_PREFIX']}{payload}",
            ),
            InlineKeyboardButton(
                t(_LANG, "reports.action_leave"),
                callback_data=f"{CB['DEV_LEAVE_CONFIRM_PREFIX']}{row.chat_id}:{source}:{page}",
            ),
            InlineKeyboardButton(
                t(_LANG, "reports.action_charge"),
                callback_data=f"{CB['DEV_LIST_CREDIT_INC_PREFIX']}{payload}",
            ),
        ],
    ]
    open_url = _resolve_open_link(row)
    if open_url:
        buttons.insert(
            0,
            [
                InlineKeyboardButton(
                    t(_LANG, "reports.action_link"),
                    url=open_url,
                ),
            ],
        )
    buttons.append(
        [
            InlineKeyboardButton(
                t(_LANG, "reports.back_to_list"),
                callback_data=f"{CB['DEV_LIST_BACK_PREFIX']}{source}:{page}",
            ),
        ]
    )
    buttons.append([InlineKeyboardButton(t(_LANG, "common.buttons.home"), callback_data=CB["WZ_HOME"])])
    return InlineKeyboardMarkup(buttons)


def _format_detail_text(row: admin_report_repo.ChatInstallRow) -> str:
    title_key = (
        "reports.group_details_title"
        if row.chat_type == "group"
        else "reports.channel_details_title"
    )
    link = row.invite_link or t(_LANG, "reports.value_unavailable")
    expire_at = (
        row.expire_at.strftime("%Y-%m-%d %H:%M")
        if row.expire_at is not None
        else t(_LANG, "reports.value_unavailable")
    )
    installed_by = (
        str(row.installed_by)
        if row.installed_by is not None
        else t(_LANG, "reports.value_unavailable")
    )
    status = (
        label(_LANG, "install_status", row.status)
        if row.status
        else t(_LANG, "reports.value_unavailable")
    )
    lines = [
        t(_LANG, title_key),
        "",
        t(
            _LANG,
            "reports.detail_body",
            title=row.title or str(row.chat_id),
            chat_id=row.chat_id,
            chat_type=label(_LANG, "chat_type", row.chat_type),
            link=link,
            credit_days=row.credit_days,
            expire_at=expire_at,
            installed_by=installed_by,
            status=status,
        ),
    ]
    return "\n".join(lines)


async def _render_dev_list_detail(
    query: CallbackQuery,
    source: str,
    chat_type: str,
    chat_id: int,
    page: int,
    *,
    notice: str | None = None,
) -> None:
    row = await admin_report_repo.get_install_row(chat_id)
    if row is None or row.chat_type != chat_type:
        await _render_dev_report_page(
            query,
            source,
            page,
            notice=t(_LANG, "reports.leave_not_found", chat_id=chat_id),
        )
        return

    text = _format_detail_text(row)
    if notice:
        text = f"{notice}\n\n{text}"
    await query.message.edit_text(
        text,
        reply_markup=_detail_kb(row, source, page),
        disable_web_page_preview=True,
    )


async def _apply_list_row_credit(
    client: Client,
    query: CallbackQuery,
    *,
    source: str,
    chat_type: str,
    chat_id: int,
    page: int,
    increase: bool,
) -> None:
    row = await admin_report_repo.get_install_row(chat_id)
    if row is None or row.chat_type != chat_type:
        await query.answer(t(_LANG, "reports.leave_not_found", chat_id=chat_id), show_alert=True)
        return

    pm_chat_id = query.message.chat.id
    resp, days = await _ask_value(
        client,
        pm_chat_id,
        "reports.enter_credit_days",
        lambda message: parse_bounded_int(
            getattr(message, "text", None),
            min_value=1,
            max_value=MAX_CREDIT_DAYS,
        ),
        t(_LANG, "reports.invalid_days"),
        user_id=query.from_user.id,
        return_to=TOKEN_DEV_LISTS,
        prompt_kwargs={"title": row.title or str(chat_id)},
    )
    if resp.message is None:
        return

    try:
        if increase:
            await CreditService.charge_managed_chat(
                chat_id,
                chat_type,
                days,
                operated_by=query.from_user.id,
                note="developer_report_list",
            )
            notice = t(
                _LANG,
                "reports.credit_updated",
                title=row.title or str(chat_id),
                amount=days,
            )
        else:
            await CreditService.adjust_managed_credit(
                chat_id,
                chat_type,
                mode="decrease",
                amount=days,
                operated_by=query.from_user.id,
                note="developer_report_list",
            )
            notice = t(
                _LANG,
                "reports.credit_deducted_row",
                title=row.title or str(chat_id),
                amount=days,
            )
    except ValueError as exc:
        if "not_managed" in str(exc):
            notice = t(_LANG, "reports.leave_not_found", chat_id=chat_id)
        else:
            logger.exception("list row credit adjustment rejected for %s", chat_id)
            notice = t(_LANG, "common.errors.try_later")
    except Exception:
        logger.exception("list row credit adjustment failed for %s", chat_id)
        notice = t(_LANG, "common.errors.try_later")

    await AdminDashboardService.invalidate_cache("lists")
    await AdminDashboardService.invalidate_cache("credit")
    await _render_dev_list_detail(query, source, chat_type, chat_id, page, notice=notice)


async def _render_dev_report_page(
    query: CallbackQuery,
    source: str,
    page: int,
    *,
    notice: str | None = None,
) -> None:
    rows, total_pages = await _fetch_report_page(source, page)
    if not rows:
        empty_key = _SOURCE_EMPTY_KEYS.get(source, "reports.list_empty")
        await query.message.edit_text(
            t(_LANG, empty_key),
            reply_markup=build_done_kb(_LANG, TOKEN_DEV_LISTS),
        )
        return

    page = max(0, min(page, total_pages - 1))
    lines = [t(_LANG, _title_key_for_source(source)), ""]
    if notice:
        lines.extend([notice, ""])

    for idx, row in enumerate(rows, start=(page * _REPORT_PAGE_SIZE) + 1):
        lines.append(_format_report_line(row, source, idx))
        lines.append("")

    lines.append(t(_LANG, "list_fmt.page_indicator", page=page + 1, total=total_pages))
    await query.message.edit_text(
        "\n".join(lines),
        reply_markup=_report_page_kb(rows, page, total_pages, source),
        disable_web_page_preview=True,
    )


_USERS_ACTIVE = "a"
_USERS_BANNED = "b"


def _users_page_kb(banned: bool, page: int, total_pages: int) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    scope = _USERS_BANNED if banned else _USERS_ACTIVE
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                t(_LANG, "reports.pagination_prev"),
                callback_data=f"{CB['PAGE_DEV_USERS_PREFIX']}{scope}:{page - 1}",
            )
        )
    if page + 1 < total_pages:
        nav.append(
            InlineKeyboardButton(
                t(_LANG, "reports.pagination_next"),
                callback_data=f"{CB['PAGE_DEV_USERS_PREFIX']}{scope}:{page + 1}",
            )
        )
    if nav:
        buttons.append(nav)
    buttons.append(
        [
            InlineKeyboardButton(
                t(_LANG, "reports.nav_back"),
                callback_data=f"{CB['WZ_BACK_PREFIX']}{TOKEN_DEV_LISTS}",
            )
        ]
    )
    buttons.append([InlineKeyboardButton(t(_LANG, "common.buttons.home"), callback_data=CB["WZ_HOME"])])
    return InlineKeyboardMarkup(buttons)


async def _render_dev_users_page(query: CallbackQuery, *, banned: bool, page: int) -> None:
    rows, total_pages, total = await admin_report_repo.get_users_page(
        page, page_size=_REPORT_PAGE_SIZE, banned=banned
    )
    if not rows:
        empty_key = "reports.inactive_users_empty" if banned else "reports.active_users_empty"
        await query.message.edit_text(
            t(_LANG, empty_key),
            reply_markup=build_done_kb(_LANG, TOKEN_DEV_LISTS),
        )
        return

    page = max(0, min(page, total_pages - 1))
    title_key = "reports.inactive_users_title" if banned else "reports.active_users_title"
    lines = [
        t(_LANG, title_key),
        "",
        t(_LANG, "reports.users_total", total=total),
        "",
    ]
    unavailable = t(_LANG, "reports.value_unavailable")
    status_key = "reports.user_status_banned" if banned else "reports.user_status_active"
    status = t(_LANG, status_key)
    for idx, row in enumerate(rows, start=(page * _REPORT_PAGE_SIZE) + 1):
        username = f"@{row.username}" if row.username else unavailable
        last_seen = (
            row.last_seen.strftime("%Y-%m-%d %H:%M")
            if row.last_seen is not None
            else unavailable
        )
        lines.append(t(_LANG, "reports.entry_header", index=idx))
        lines.append(
            t(
                _LANG,
                "reports.user_item",
                user_id=row.user_id,
                name=row.first_name or unavailable,
                username=username,
                status=status,
                last_seen=last_seen,
            )
        )
        lines.append("")

    lines.append(t(_LANG, "list_fmt.page_indicator", page=page + 1, total=total_pages))
    await query.message.edit_text(
        "\n".join(lines),
        reply_markup=_users_page_kb(banned, page, total_pages),
        disable_web_page_preview=True,
    )


def _parse_leave_payload(data: str, prefix: str) -> tuple[int, str, int] | None:
    if not data.startswith(prefix):
        return None
    raw = data[len(prefix):]
    parts = raw.split(":")
    if len(parts) != 3:
        return None
    chat_id = parse_user_id(parts[0])
    if chat_id is None:
        return None
    source = parts[1]
    try:
        page = int(parts[2])
    except ValueError:
        return None
    return chat_id, source, max(page, 0)


def _leave_confirm_kb(chat_id: int, source: str, page: int) -> InlineKeyboardMarkup:
    payload = f"{chat_id}:{source}:{page}"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t(_LANG, "reports.leave_confirm_yes"),
                    callback_data=f"{CB['DEV_LEAVE_EXEC_PREFIX']}{payload}",
                ),
                InlineKeyboardButton(
                    t(_LANG, "reports.leave_confirm_no"),
                    callback_data=f"{CB['DEV_LEAVE_CANCEL_PREFIX']}{payload}",
                ),
            ],
            [
                InlineKeyboardButton(
                    t(_LANG, "common.buttons.back"),
                    callback_data=f"{CB['WZ_BACK_PREFIX']}{TOKEN_DEV_LISTS}",
                )
            ],
            [InlineKeyboardButton(t(_LANG, "common.buttons.home"), callback_data=CB["WZ_HOME"])],
        ]
    )


async def _leave_install(client: Client, chat_id: int, acted_by: int) -> admin_report_repo.ChatInstallRow | None:
    row = await admin_report_repo.get_install_row(chat_id)
    if row is None:
        return None

    try:
        await client.leave_chat(chat_id)
    except Exception:
        logger.debug("Could not leave chat %s", chat_id)

    if row.chat_type == "group":
        await group_repo.deactivate_group(chat_id)
    else:
        await channel_repo.deactivate_channel(chat_id)

    await log_repo.log_install(
        chat_id=chat_id,
        chat_title=row.title,
        chat_type=row.chat_type,
        triggered_by=acted_by,
        sudo_id=None,
        action="leave",
    )

    try:
        await NotificationService.notify_uninstall(client, chat_id, row.chat_type, row.title)
    except Exception:
        logger.debug("Could not notify uninstall for %s", chat_id)

    await AdminDashboardService.invalidate_cache("lists")
    await AdminDashboardService.invalidate_cache("credit")
    return row


def _force_join_status_text(status: str) -> str:
    if status == "ok":
        return t(_LANG, "force_join_mgmt.status_ok")
    if status == "pending":
        return t(_LANG, "force_join_mgmt.status_pending")
    if status == "bot_not_admin":
        return t(_LANG, "force_join_mgmt.status_bot_not_admin")
    if status == "inaccessible":
        return t(_LANG, "force_join_mgmt.status_inaccessible")
    return t(_LANG, "force_join_mgmt.status_unknown")


def _force_join_badge(status: str) -> str:
    if status == "ok":
        return t(_LANG, "admin.fm.badge_ok")
    if status == "pending":
        return t(_LANG, "admin.fm.badge_pending")
    return t(_LANG, "admin.fm.badge_broken")


def _force_join_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(t(_LANG, "force_join_mgmt.add_btn"), callback_data=CB["DEV_FORCE_JOIN_ADD"])],
            [InlineKeyboardButton(t(_LANG, "force_join_mgmt.list_btn"), callback_data=CB["DEV_FORCE_JOIN_LIST"])],
            [
                InlineKeyboardButton(
                    t(_LANG, "common.buttons.back"),
                    callback_data=f"{CB['WZ_BACK_PREFIX']}{TOKEN_DEV_FORCE_JOIN}",
                )
            ],
            [InlineKeyboardButton(t(_LANG, "common.buttons.home"), callback_data=CB["WZ_HOME"])],
        ]
    )


def _force_join_list_kb(
    channels: list,
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    for channel in channels:
        rows.append(
            [
                InlineKeyboardButton(
                    t(_LANG, "force_join_mgmt.remove_item_btn", channel_id=channel.channel_id),
                    callback_data=f"{CB['DEV_FORCE_JOIN_REMOVE_PREFIX']}{channel.channel_id}:{page}",
                )
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                t(_LANG, "common.buttons.prev"),
                callback_data=f"{CB['DEV_FORCE_JOIN_PAGE_PREFIX']}{page - 1}",
            )
        )
    if page + 1 < total_pages:
        nav.append(
            InlineKeyboardButton(
                t(_LANG, "common.buttons.next"),
                callback_data=f"{CB['DEV_FORCE_JOIN_PAGE_PREFIX']}{page + 1}",
            )
        )
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(t(_LANG, "force_join_mgmt.add_btn"), callback_data=CB["DEV_FORCE_JOIN_ADD"])])
    rows.append([InlineKeyboardButton(t(_LANG, "force_join_mgmt.list_btn"), callback_data=CB["DEV_FORCE_JOIN_LIST"])])
    rows.append(
        [
            InlineKeyboardButton(
                t(_LANG, "common.buttons.back"),
                callback_data=f"{CB['WZ_BACK_PREFIX']}{TOKEN_DEV_FORCE_JOIN}",
            )
        ]
    )
    rows.append([InlineKeyboardButton(t(_LANG, "common.buttons.home"), callback_data=CB["WZ_HOME"])])
    return InlineKeyboardMarkup(rows)


async def _render_force_join_manage_home(query: CallbackQuery, *, notice: str | None = None) -> None:
    summary = await AdminDashboardService.get_general_summary()
    lines = [
        t(_LANG, "force_join_mgmt.menu_title"),
        "",
        t(_LANG, "force_join_mgmt.menu_desc"),
        "",
        _summary_row("status.force_join_label", _bool_label(bool(summary["force_join_enabled"]))),
        _summary_row("panels.developer.summary_required_channels", int(summary["required_channels"])),
    ]
    if notice:
        lines.extend(["", notice])

    await query.message.edit_text(
        "\n".join(lines),
        reply_markup=_force_join_menu_kb(),
    )


async def _render_force_join_list(
    query: CallbackQuery,
    page: int,
    *,
    notice: str | None = None,
) -> None:
    channels, total_pages = await force_join_repo.get_active_targets_page(page, page_size=10)
    if not channels:
        await query.message.edit_text(
            t(_LANG, "force_join_mgmt.list_empty"),
            reply_markup=_force_join_menu_kb(),
        )
        return

    page = max(0, min(page, total_pages - 1))
    lines = [t(_LANG, "force_join_mgmt.list_title"), ""]
    if notice:
        lines.extend([notice, ""])

    for idx, channel in enumerate(channels, start=(page * 10) + 1):
        lines.append(
            t(
                _LANG,
                "force_join_mgmt.list_item",
                index=idx,
                badge=_force_join_badge(channel.verify_status),
                title=channel.display_name or str(channel.channel_id),
                channel_id=channel.channel_id,
                username=(f"@{channel.channel_username}" if channel.channel_username else t(_LANG, "reports.value_unavailable")),
                status=_force_join_status_text(channel.verify_status),
            )
        )

    lines.append("")
    lines.append(t(_LANG, "list_fmt.page_indicator", page=page + 1, total=total_pages))
    await query.message.edit_text(
        "\n".join(lines),
        reply_markup=_force_join_list_kb(channels, page, total_pages),
    )


def _parse_force_join_remove_request(data: str) -> tuple[int, int] | None:
    prefix = CB["DEV_FORCE_JOIN_REMOVE_PREFIX"]
    if not data.startswith(prefix):
        return None
    if data.startswith(CB["DEV_FORCE_JOIN_REMOVE_EXEC_PREFIX"]) or data.startswith(
        CB["DEV_FORCE_JOIN_REMOVE_ABORT_PREFIX"]
    ):
        return None
    raw = data[len(prefix):]
    parts = raw.split(":")
    if len(parts) != 2:
        return None
    channel_id = parse_user_id(parts[0])
    if channel_id is None:
        return None
    try:
        page = int(parts[1])
    except ValueError:
        return None
    return channel_id, max(page, 0)


def _parse_fj_remove_bound(data: str, prefix: str) -> tuple[int, int, int, int] | None:
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix):].split(":")
    if len(parts) != 4:
        return None
    channel_id = parse_user_id(parts[0])
    if channel_id is None:
        return None
    try:
        page = int(parts[1])
        user_id = int(parts[2])
        issued_at = int(parts[3])
    except ValueError:
        return None
    if user_id <= 0 or issued_at <= 0:
        return None
    return channel_id, max(page, 0), user_id, issued_at


def _is_stale_fj_remove_token(issued_at: int) -> bool:
    now = int(time.time())
    return issued_at > now + 60 or now - issued_at > _FJ_REMOVE_CONFIRM_TTL_SECONDS


def _is_stale_dev_confirm_token(issued_at: int) -> bool:
    now = int(time.time())
    return issued_at > now + 60 or now - issued_at > _DEV_CONFIRM_TTL_SECONDS


def _parse_media_cleanup_bound(data: str, prefix: str) -> tuple[int, int] | None:
    if not data.startswith(prefix):
        return None
    raw = data[len(prefix) :]
    parts = raw.split(":", 1)
    if len(parts) != 2:
        return None
    try:
        user_id = int(parts[0])
        issued_at = int(parts[1])
    except ValueError:
        return None
    if user_id <= 0 or issued_at <= 0:
        return None
    return user_id, issued_at


async def _render_bot_update_panel(query: CallbackQuery) -> None:
    snapshot = await build_diagnostics_snapshot()
    text = format_bot_update_panel(_LANG, snapshot)
    await query.message.edit_text(
        text,
        reply_markup=KeyboardFactory.dev_bot_update(
            _LANG,
            reload_available=reload_is_available(),
        ),
    )


async def _render_media_health_panel(query: CallbackQuery) -> None:
    snapshot = await build_health_snapshot()
    text = format_health_report(_LANG, snapshot)
    await query.message.edit_text(
        text,
        reply_markup=KeyboardFactory.dev_media_health(_LANG),
    )


def _parse_privileged_remove_bound(data: str, prefix: str) -> tuple[int, int, int] | None:
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix) :].split(":")
    if len(parts) != 3:
        return None
    target_uid = parse_user_id(parts[0])
    if target_uid is None:
        return None
    try:
        user_id = int(parts[1])
        issued_at = int(parts[2])
    except ValueError:
        return None
    if user_id <= 0 or issued_at <= 0:
        return None
    return target_uid, user_id, issued_at


def _parse_privileged_remove_abort(data: str, prefix: str) -> tuple[int, int] | None:
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix) :].split(":")
    if len(parts) != 2:
        return None
    try:
        user_id = int(parts[0])
        issued_at = int(parts[1])
    except ValueError:
        return None
    if user_id <= 0 or issued_at <= 0:
        return None
    return user_id, issued_at


def _parse_text_clear_bound(data: str, prefix: str) -> tuple[str, int, int] | None:
    if not data.startswith(prefix):
        return None
    raw = data[len(prefix) :]
    parts = raw.rsplit(":", 2)
    if len(parts) != 3:
        return None
    field, user_raw, issued_raw = parts
    if get_field_spec(field) is None:
        return None
    try:
        user_id = int(user_raw)
        issued_at = int(issued_raw)
    except ValueError:
        return None
    if user_id <= 0 or issued_at <= 0:
        return None
    return field, user_id, issued_at


def _fj_remove_confirm_kb(
    channel_id: int,
    page: int,
    user_id: int,
    issued_at: int,
) -> InlineKeyboardMarkup:
    payload = f"{channel_id}:{page}:{user_id}:{issued_at}"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t(_LANG, "reports.leave_confirm_yes"),
                    callback_data=f"{CB['DEV_FORCE_JOIN_REMOVE_EXEC_PREFIX']}{payload}",
                ),
                InlineKeyboardButton(
                    t(_LANG, "reports.leave_confirm_no"),
                    callback_data=f"{CB['DEV_FORCE_JOIN_REMOVE_ABORT_PREFIX']}{payload}",
                ),
            ],
            [
                InlineKeyboardButton(
                    t(_LANG, "common.buttons.back"),
                    callback_data=f"{CB['WZ_BACK_PREFIX']}{TOKEN_DEV_FORCE_JOIN}",
                )
            ],
            [InlineKeyboardButton(t(_LANG, "common.buttons.home"), callback_data=CB["WZ_HOME"])],
        ]
    )



def _build_blacklist_text(entries: list) -> str:
    if not entries:
        return t(_LANG, "blacklist_mgmt.list_empty")

    lines = [t(_LANG, "blacklist_mgmt.list_title"), ""]
    for entry in entries:
        lines.append(
            t(
                _LANG,
                "blacklist_mgmt.item",
                entity_type=getattr(entry, "entity_type", "user"),
                entity=str(getattr(entry, "entity_id", 0)),
            )
        )
    return "\n".join(lines)


async def render_install_policy_panel(client: Client, query: CallbackQuery) -> bool:
    """Render install policy home via panel_callback_edit."""
    policy = await InstallPolicyService.get_policy()
    mode = policy.policy_mode if policy else "open"
    text = (
        t(_LANG, "admin.install_policy.title")
        + "\n"
        + t(
            _LANG,
            "admin.install_policy.current_mode",
            mode=label(_LANG, "install_policy_mode", mode),
        )
    )
    return await panel_callback_edit(
        client,
        query,
        text,
        KeyboardFactory.install_policy_panel(_LANG),
    )


async def _deliver_install_policy_outcome(
    client: Client,
    query: CallbackQuery,
    notice: str,
    *,
    whitelist: bool = False,
) -> None:
    if whitelist:
        text = f"{notice}\n\n{t(_LANG, 'admin.install_policy.whitelist_title')}"
        reply_markup = KeyboardFactory.install_policy_whitelist_panel(_LANG)
    else:
        policy = await InstallPolicyService.get_policy()
        mode = policy.policy_mode if policy else "open"
        text = (
            f"{notice}\n\n"
            f"{t(_LANG, 'admin.install_policy.title')}\n"
            f"{t(_LANG, 'admin.install_policy.current_mode', mode=label(_LANG, 'install_policy_mode', mode))}"
        )
        reply_markup = KeyboardFactory.install_policy_panel(_LANG)
    await deliver_ask_outcome(
        client,
        query.message.chat.id,
        query.from_user.id,
        text,
        reply_markup,
    )


def register(bot: Client, call_py) -> None:  # noqa: ARG001 – call_py unused here

    @bot.on_callback_query(
        filters.regex(
            r"^dev:(?:cat:rates$|rate:|invoice$|inv:hist$|"
            r"monthly_invoice(?:$|:))"
        )
        & _pm_dev,
        # Stale financial buttons must be handled before legacy panel
        # handlers below can prompt for, persist, or send payment data.
        group=REMOVED_FINANCIAL_CALLBACK_GROUP,
    )
    @developer_only
    async def dev_removed_financial_surface(
        client: Client,
        query: CallbackQuery,
    ):  # noqa: ARG001
        await query.answer(
            t(_LANG, "credit.financial_surface_removed"),
            show_alert=True,
        )
        if hasattr(query, "stop_propagation"):
            query.stop_propagation()

    # ── Status ────────────────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_STATUS']}$") & _pm_dev)
    @developer_only
    async def dev_status(client: Client, query: CallbackQuery):
        await query.answer()
        groups = await group_repo.get_all_active_groups()
        from app.database.engine import async_session
        from app.database.models import Channel, User
        from sqlalchemy import func, select

        async with async_session() as session:
            ch_count = (
                await session.execute(
                    select(func.count()).select_from(Channel).where(Channel.status == "active")
                )
            ).scalar() or 0
            u_count = (await session.execute(select(func.count()).select_from(User))).scalar() or 0

        sudos = await user_repo.get_all_sudos()
        from app.services import HelperPoolService

        helpers = await HelperPoolService.get_all_helpers()
        active = CallService.get_active_calls()

        import asyncio
        import time
        loop = asyncio.get_event_loop()

        def _get_sys_metrics():
            import psutil
            cpu = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            boot = psutil.boot_time()
            uptime_sec = int(time.time() - boot)
            hours, rem = divmod(uptime_sec, 3600)
            minutes, _ = divmod(rem, 60)
            return {
                "cpu": cpu,
                "ram_used": mem.used // (1024 * 1024),
                "ram_total": mem.total // (1024 * 1024),
                "ram_pct": mem.percent,
                "disk_used": disk.used // (1024**3),
                "disk_total": disk.total // (1024**3),
                "disk_pct": disk.percent,
                "uptime_hours": hours,
                "uptime_minutes": minutes,
            }

        metrics = await loop.run_in_executor(None, _get_sys_metrics)
        uptime = t(
            _LANG,
            "status.uptime_fmt",
            hours=metrics.pop("uptime_hours"),
            minutes=metrics.pop("uptime_minutes"),
        )

        text = t(
            _LANG,
            "status.bot_info",
            groups=len(groups),
            channels=ch_count,
            users=u_count,
            sudos=len(sudos),
            helpers=len(helpers),
            active_calls=len(active),
            uptime=uptime,
            **metrics,
        )
        await query.message.edit_text(text, reply_markup=KeyboardFactory.developer_panel(_LANG))

    # ── Sub-menu category handlers ────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_CAT_CREDIT']}$") & _pm_dev)
    @developer_only
    async def dev_cat_credit(client: Client, query: CallbackQuery):
        await panel_callback_edit(
            client,
            query,
            await _build_dev_credit_text(),
            KeyboardFactory.dev_sub_credit(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_CAT_MONTHLY_INVOICE']}$") & _pm_dev)
    @developer_only
    async def dev_cat_monthly_invoice(client: Client, query: CallbackQuery):
        await panel_callback_edit(
            client,
            query,
            await _build_dev_monthly_invoice_text(),
            await _dev_monthly_invoice_keyboard(),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_MONTHLY_INVOICE_CONFIG']}$") & _pm_dev)
    @developer_only
    async def dev_monthly_invoice_config(client: Client, query: CallbackQuery):
        await panel_callback_edit(
            client,
            query,
            await _build_dev_monthly_invoice_text(),
            await _dev_monthly_invoice_keyboard(),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_MONTHLY_INVOICE_SET_AMOUNT']}$") & _pm_dev)
    @developer_only
    async def dev_monthly_invoice_set_amount(client: Client, query: CallbackQuery):
        await query.answer()
        await remember_panel_from_query(query)
        chat_id = query.message.chat.id
        user_id = query.from_user.id
        resp = await _ask(
            client,
            chat_id,
            "monthly_invoice.amount_prompt",
            user_id=user_id,
            return_to=TOKEN_DEV_MONTHLY_INVOICE,
        )
        if await notify_ask_abort(
            client,
            chat_id,
            resp,
            return_to=TOKEN_DEV_MONTHLY_INVOICE,
            lang=_LANG,
            user_id=user_id,
        ):
            return

        raw_amount = getattr(resp.message, "text", "")
        amount = parse_bounded_int(
            str(raw_amount).replace(",", ""),
            min_value=1,
            max_value=MAX_WALLET_AMOUNT,
        )
        if amount is None:
            await _send_done(
                client,
                chat_id,
                t(_LANG, "monthly_invoice.amount_invalid"),
                TOKEN_DEV_MONTHLY_INVOICE,
                user_id=user_id,
            )
            return

        await settings_repo.set_bot_setting(
            monthly_invoice_service.MONTHLY_INVOICE_AMOUNT_KEY,
            str(amount),
            updated_by=user_id,
        )
        await _send_done(
            client,
            chat_id,
            t(_LANG, "monthly_invoice.amount_saved", amount=amount),
            TOKEN_DEV_MONTHLY_INVOICE,
            user_id=user_id,
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_MONTHLY_INVOICE_PREPARE']}$") & _pm_dev)
    @developer_only
    async def dev_monthly_invoice_prepare(client: Client, query: CallbackQuery):
        await query.answer()
        result = await monthly_invoice_service.create_current_month_invoice_previews(
            bot_identifier=t(_LANG, "monthly_invoice.bot_identifier_default"),
            developer_id=query.from_user.id,
            lang=_LANG,
        )
        await query.message.edit_text(
            _render_monthly_invoice_prepare_result(result),
            reply_markup=build_done_kb(_LANG, TOKEN_DEV_MONTHLY_INVOICE),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_MONTHLY_INVOICE_SEND']}$") & _pm_dev)
    @developer_only
    async def dev_monthly_invoice_send(client: Client, query: CallbackQuery):
        await query.answer()
        result = await monthly_invoice_service.send_prepared_monthly_invoices(
            client,
            lang=_LANG,
        )
        await query.message.edit_text(
            _render_monthly_invoice_send_result(result),
            reply_markup=build_done_kb(_LANG, TOKEN_DEV_MONTHLY_INVOICE),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_MONTHLY_INVOICE_AUTO_TOGGLE']}$") & _pm_dev)
    @developer_only
    async def dev_monthly_invoice_auto_toggle(client: Client, query: CallbackQuery):
        await query.answer()
        enabled = await monthly_invoice_service.get_monthly_invoice_auto_send_enabled()
        new_value = "0" if enabled else "1"
        await settings_repo.set_bot_setting(
            monthly_invoice_service.MONTHLY_INVOICE_AUTO_SEND_ENABLED_KEY,
            new_value,
            updated_by=query.from_user.id,
        )
        status = t(
            _LANG,
            "monthly_invoice.auto_send_enabled_label"
            if new_value == "1"
            else "monthly_invoice.auto_send_disabled_label",
        )
        text = "\n\n".join(
            [
                t(_LANG, "monthly_invoice.auto_send_toggle_saved", status=status),
                await _build_dev_monthly_invoice_text(),
            ]
        )
        await query.message.edit_text(
            text,
            reply_markup=await _dev_monthly_invoice_keyboard(),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_MONTHLY_INVOICE_LIST']}$") & _pm_dev)
    @developer_only
    async def dev_monthly_invoice_list(client: Client, query: CallbackQuery):
        await query.answer()
        invoices = await monthly_invoice_repo.list_recent_invoices(limit=10)
        text, kb = _render_monthly_invoice_list(invoices)
        await query.message.edit_text(text, reply_markup=kb)

    @bot.on_callback_query(
        filters.regex(rf"^{re.escape(CB['DEV_MONTHLY_INVOICE_DETAIL_PREFIX'])}\d+$") & _pm_dev
    )
    @developer_only
    async def dev_monthly_invoice_detail(client: Client, query: CallbackQuery):
        await query.answer()
        invoice_id = parse_bounded_int(
            query.data[len(CB["DEV_MONTHLY_INVOICE_DETAIL_PREFIX"]) :],
            min_value=1,
            max_value=2_147_483_647,
        )
        if invoice_id is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        invoice = await monthly_invoice_repo.get_invoice_by_id(invoice_id)
        if invoice is None:
            await query.message.edit_text(
                t(_LANG, "monthly_invoice.detail_not_found"),
                reply_markup=build_done_kb(_LANG, TOKEN_DEV_MONTHLY_INVOICE),
            )
            return
        await query.message.edit_text(
            _render_monthly_invoice_detail(invoice),
            reply_markup=build_done_kb(_LANG, TOKEN_DEV_MONTHLY_INVOICE),
        )

    @bot.on_callback_query(
        filters.regex(rf"^{re.escape(CB['DEV_MONTHLY_INVOICE_DETAIL_PREFIX'])}(?!\d+$).*") & _pm_dev
    )
    @developer_only
    async def dev_monthly_invoice_detail_malformed(client: Client, query: CallbackQuery):
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_CAT_RATES']}$") & _pm_dev)
    @developer_only
    async def dev_cat_rates(client: Client, query: CallbackQuery):
        await panel_callback_edit(
            client,
            query,
            t(_LANG, "panels.developer.cat_rates_title"),
            KeyboardFactory.dev_sub_rates(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_CAT_BROADCAST']}$") & _pm_dev)
    @developer_only
    async def dev_cat_broadcast(client: Client, query: CallbackQuery):
        await panel_callback_edit(
            client,
            query,
            t(_LANG, "panels.developer.cat_broadcast_title"),
            KeyboardFactory.dev_sub_broadcast(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_CAT_LISTS']}$") & _pm_dev)
    @developer_only
    async def dev_cat_lists(client: Client, query: CallbackQuery):
        await panel_callback_edit(
            client,
            query,
            await _build_dev_lists_text(),
            KeyboardFactory.dev_sub_lists(_LANG),
        )

    # Identity-only filter so a stale panel (query.message is None -> _pm_dev
    # False) cannot skip this handler and leak `hlp:home` to the `^hlp:`
    # fallback. Dev is enforced by @developer_only; private scope is enforced
    # explicitly below (this shortcut has no _guard_private of its own).
    @bot.on_callback_query(filters.regex(f"^{CB['HLP_HOME']}$"))
    @developer_only
    async def dev_shortcut_helper_home(client: Client, query: CallbackQuery):
        chat = query.message.chat if query.message else None
        if chat is None or getattr(chat.type, "value", chat.type) != "private":
            await query.answer(
                t(_LANG, "admin.helpers.private_only"), show_alert=True
            )
            return
        await render_helper_home_panel(client, query)

    @bot.on_callback_query(filters.regex(f"^{CB['AN_HOME']}$") & _pm_dev)
    @developer_only
    async def dev_shortcut_analytics_home(client: Client, query: CallbackQuery):
        await render_analytics_home_panel(client, query)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_ABOUT']}$") & _pm_dev)
    @developer_only
    async def dev_about(client: Client, query: CallbackQuery):
        text = await get_about_text(_LANG)
        await panel_callback_edit(
            client,
            query,
            text,
            KeyboardFactory.back_button(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['HELP_HOME']}$") & _pm_dev)
    @developer_only
    async def dev_shortcut_help_about(client: Client, query: CallbackQuery):
        await render_help_home_panel(client, query)

    @bot.on_callback_query(filters.regex(f"^{CB['BCW_START']}$") & _pm_dev)
    @developer_only
    async def dev_shortcut_bcw_start(client: Client, query: CallbackQuery):
        await begin_broadcast_wizard(client, query)

    @bot.on_callback_query(filters.regex(f"^{CB['BC_HISTORY']}$") & _pm_dev)
    @developer_only
    async def dev_shortcut_bc_history(client: Client, query: CallbackQuery):
        await render_broadcast_history_panel(client, query)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_CAT_SETTINGS']}$") & _pm_dev)
    @developer_only
    async def dev_cat_settings(client: Client, query: CallbackQuery):
        await query.answer()
        await _show_dev_settings_menu(query)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_CAT_FORCE_JOIN']}$") & _pm_dev)
    @developer_only
    async def dev_cat_force_join(client: Client, query: CallbackQuery):
        await query.answer()
        await _show_dev_force_join_menu(query)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_CAT_MODERATION']}$") & _pm_dev)
    @developer_only
    async def dev_cat_moderation(client: Client, query: CallbackQuery):
        await panel_callback_edit(
            client,
            query,
            await _build_dev_moderation_text(),
            KeyboardFactory.dev_sub_moderation(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_CAT_USERS']}$") & _pm_dev)
    @developer_only
    async def dev_cat_users(client: Client, query: CallbackQuery):
        await panel_callback_edit(
            client,
            query,
            await _build_dev_users_text(),
            KeyboardFactory.dev_sub_users(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_ADMIN_TITLES']}$") & _pm_dev)
    @developer_only
    async def dev_admin_titles(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        await _show_admin_title_settings(query)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_TITLE_APPLY_DEV']}$") & _pm_dev)
    @developer_only
    async def dev_title_apply_developer(client: Client, query: CallbackQuery):
        await _start_admin_title_apply(client, query, "d", query.from_user.id, 0)

    @bot.on_callback_query(filters.regex(r"^dev:title:apply:own:-?\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_title_apply_owner(client: Client, query: CallbackQuery):
        parsed = _parse_admin_title_role_target(query.data, CB["DEV_TITLE_APPLY_OWNER_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        target_id, page = parsed
        await _start_admin_title_apply(client, query, "o", target_id, page)

    @bot.on_callback_query(filters.regex(r"^dev:title:apply:sudo:-?\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_title_apply_sudo(client: Client, query: CallbackQuery):
        parsed = _parse_admin_title_role_target(query.data, CB["DEV_TITLE_APPLY_SUDO_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        target_id, page = parsed
        await _start_admin_title_apply(client, query, "s", target_id, page)

    @bot.on_callback_query(filters.regex(r"^dev:title:apply:do:[A-Za-z0-9_-]{8,24}:\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_title_apply_confirm(client: Client, query: CallbackQuery):
        parsed = _parse_admin_title_apply_confirm(query.data, CB["DEV_TITLE_APPLY_DO_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        token, actor_id, issued_at = parsed
        if query.from_user.id != actor_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_dev_confirm_token(issued_at):
            await _delete_admin_title_apply_context(token)
            await query.answer(t(_LANG, "admin_titles.stale_confirm"), show_alert=True)
            return
        context = await _get_admin_title_apply_context(token)
        if context is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        kind, target_id, chat_id, page = context
        await _delete_admin_title_apply_context(token)
        target_user_id = _admin_title_apply_target_id(kind, target_id)
        back_callback = _admin_title_apply_return_callback(kind, target_id, page)
        title, error_key = await _get_admin_title_for_apply(kind, target_id)
        if error_key is not None or title is None:
            await query.answer(t(_LANG, error_key or "admin_titles.apply_title_not_set"), show_alert=True)
            return
        preflight = await preflight_admin_title_apply(client, chat_id, target_user_id)
        if not preflight.ok:
            await query.message.edit_text(
                t(_LANG, preflight.message_key),
                reply_markup=_admin_title_done_kb(back_callback),
            )
            return
        try:
            await apply_admin_title(client, chat_id, target_user_id, title)
        except ValueError:
            await query.message.edit_text(
                t(_LANG, "admin_titles.apply_target_not_admin"),
                reply_markup=_admin_title_done_kb(back_callback),
            )
            return
        except Exception as exc:
            exc_name = type(exc).__name__
            if "FloodWait" in exc_name:
                wait = getattr(exc, "value", 0)
                await query.message.edit_text(
                    t(_LANG, "admin_titles.apply_failed_flood", wait=wait),
                    reply_markup=_admin_title_done_kb(back_callback),
                )
                return
            await query.message.edit_text(
                t(_LANG, "admin_titles.apply_failed"),
                reply_markup=_admin_title_done_kb(back_callback),
            )
            return
        await query.answer(t(_LANG, "admin_titles.apply_success"), show_alert=True)
        await query.message.edit_text(
            t(_LANG, "admin_titles.apply_success"),
            reply_markup=_admin_title_done_kb(back_callback),
        )

    @bot.on_callback_query(filters.regex(r"^dev:title:apply:no:[A-Za-z0-9_-]{8,24}:\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_title_apply_abort(client: Client, query: CallbackQuery):  # noqa: ARG001
        parsed = _parse_admin_title_apply_confirm(query.data, CB["DEV_TITLE_APPLY_NO_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        token, actor_id, issued_at = parsed
        if query.from_user.id != actor_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_dev_confirm_token(issued_at):
            await _delete_admin_title_apply_context(token)
            await query.answer(t(_LANG, "admin_titles.stale_confirm"), show_alert=True)
            return
        context = await _get_admin_title_apply_context(token)
        if context is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await _delete_admin_title_apply_context(token)
        await query.answer(t(_LANG, "admin_titles.apply_cancelled"), show_alert=False)
        kind, target_id, _chat_id, page = context
        if kind == "s":
            await _render_sudo_detail(query, target_id, page)
            return
        if kind == "o":
            await _render_owner_title_list(query, page)
            return
        await _show_admin_title_settings(query)

    @bot.on_callback_query(filters.regex(r"^dev:title:apply:(?:do|no):.*") & _pm_dev)
    @developer_only
    async def dev_title_apply_malformed(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_TGPROM_DEV']}$") & _pm_dev)
    @developer_only
    async def dev_tg_prom_developer(client: Client, query: CallbackQuery):
        await _start_manual_telegram_promotion(client, query, "d", query.from_user.id, 0)

    @bot.on_callback_query(filters.regex(r"^dev:tgprom:own:-?\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_tg_prom_owner(client: Client, query: CallbackQuery):
        parsed = _parse_admin_title_role_target(query.data, CB["DEV_TGPROM_OWNER_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        target_id, page = parsed
        await _start_manual_telegram_promotion(client, query, "o", target_id, page)

    @bot.on_callback_query(filters.regex(r"^dev:tgprom:sudo:-?\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_tg_prom_sudo(client: Client, query: CallbackQuery):
        parsed = _parse_admin_title_role_target(query.data, CB["DEV_TGPROM_SUDO_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        target_id, page = parsed
        await _start_manual_telegram_promotion(client, query, "s", target_id, page)

    @bot.on_callback_query(filters.regex(r"^dev:tgprom:do:[A-Za-z0-9_-]{8,24}:\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_tg_prom_confirm(client: Client, query: CallbackQuery):
        parsed = _parse_admin_title_apply_confirm(query.data, CB["DEV_TGPROM_DO_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        token, actor_id, issued_at = parsed
        if query.from_user.id != actor_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_dev_confirm_token(issued_at):
            await _delete_tg_prom_context(token)
            await query.answer(t(_LANG, "admin_titles.stale_confirm"), show_alert=True)
            return
        context = await _get_tg_prom_context(token)
        if context is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        kind, target_id, chat_id, page = context
        await _delete_tg_prom_context(token)
        await _execute_manual_telegram_promotion(client, query, kind, target_id, chat_id, page)

    @bot.on_callback_query(filters.regex(r"^dev:tgprom:no:[A-Za-z0-9_-]{8,24}:\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_tg_prom_abort(client: Client, query: CallbackQuery):  # noqa: ARG001
        parsed = _parse_admin_title_apply_confirm(query.data, CB["DEV_TGPROM_NO_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        token, actor_id, issued_at = parsed
        if query.from_user.id != actor_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_dev_confirm_token(issued_at):
            await _delete_tg_prom_context(token)
            await query.answer(t(_LANG, "admin_titles.stale_confirm"), show_alert=True)
            return
        context = await _get_tg_prom_context(token)
        if context is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await _delete_tg_prom_context(token)
        await query.answer(t(_LANG, "admin_titles.prom_cancelled"), show_alert=False)
        kind, target_id, _chat_id, page = context
        if kind == "s":
            await _render_sudo_detail(query, target_id, page)
            return
        if kind == "o":
            await _render_owner_title_list(query, page)
            return
        await _show_admin_title_settings(query)

    @bot.on_callback_query(filters.regex(r"^dev:tgprom:(?:do|no):.*") & _pm_dev)
    @developer_only
    async def dev_tg_prom_malformed(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_TITLE_DEV_SET']}$") & _pm_dev)
    @developer_only
    async def dev_title_developer_set(client: Client, query: CallbackQuery):
        await query.answer()

        def _parse_title(message) -> str | None:
            title, error_key = normalize_admin_title(getattr(message, "text", None))
            return title if error_key is None else None

        resp, title = await _ask_value(
            client,
            query.message.chat.id,
            "admin_titles.enter_title",
            _parse_title,
            t(_LANG, "admin_titles.invalid"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_USERS,
            prompt_kwargs={"max": str(_ADMIN_TITLE_MAX_LEN)},
        )
        if resp.message is None:
            return
        await settings_repo.set_bot_setting(
            settings_repo.DEVELOPER_ADMIN_TITLE_KEY,
            title,
            updated_by=query.from_user.id,
        )
        await _send_admin_title_result(
            client,
            query.message.chat.id,
            t(_LANG, "admin_titles.saved"),
            CB["DEV_ADMIN_TITLES"],
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_TITLE_DEV_CLEAR']}$") & _pm_dev)
    @developer_only
    async def dev_title_developer_clear(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        issued_at = int(time.time())
        payload = f"{query.from_user.id}:{issued_at}"
        await query.message.edit_text(
            t(_LANG, "admin_titles.clear_confirm"),
            reply_markup=_admin_title_clear_confirm_kb(
                "dev",
                payload,
                CB["DEV_ADMIN_TITLES"],
            ),
        )

    @bot.on_callback_query(filters.regex(r"^dev:title:dev:clear:do:\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_title_developer_clear_confirm(client: Client, query: CallbackQuery):  # noqa: ARG001
        parsed = _parse_admin_title_dev_clear(query.data, CB["DEV_TITLE_DEV_CLEAR_DO_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        actor_id, issued_at = parsed
        if query.from_user.id != actor_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_dev_confirm_token(issued_at):
            await query.answer(t(_LANG, "admin_titles.stale_confirm"), show_alert=True)
            return
        await settings_repo.set_bot_setting(
            settings_repo.DEVELOPER_ADMIN_TITLE_KEY,
            None,
            updated_by=query.from_user.id,
        )
        await query.answer(t(_LANG, "admin_titles.cleared"), show_alert=True)
        await _show_admin_title_settings(query)

    @bot.on_callback_query(filters.regex(r"^dev:title:dev:clear:no:\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_title_developer_clear_abort(client: Client, query: CallbackQuery):  # noqa: ARG001
        parsed = _parse_admin_title_dev_clear(query.data, CB["DEV_TITLE_DEV_CLEAR_NO_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        actor_id, issued_at = parsed
        if query.from_user.id != actor_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_dev_confirm_token(issued_at):
            await query.answer(t(_LANG, "admin_titles.stale_confirm"), show_alert=True)
            return
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        await _show_admin_title_settings(query)

    @bot.on_callback_query(filters.regex(r"^dev:title:dev:clear:(?:do|no):.*") & _pm_dev)
    @developer_only
    async def dev_title_developer_clear_malformed(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)

    @bot.on_callback_query(filters.regex(r"^dev:title:own:list:\d+$") & _pm_dev)
    @developer_only
    async def dev_title_owner_list(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        page = int(query.data.split(":")[-1])
        await _render_owner_title_list(query, page)

    @bot.on_callback_query(filters.regex(r"^dev:title:own:set:-?\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_title_owner_set(client: Client, query: CallbackQuery):
        parsed = _parse_admin_title_role_target(query.data, CB["DEV_TITLE_OWNER_SET_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        target_id, page = parsed
        owner = await user_repo.get_owner(target_id)
        if owner is None:
            await query.answer(t(_LANG, "owner_mgmt.not_found"), show_alert=True)
            return
        await query.answer()
        def _parse_title(message) -> str | None:
            title, error_key = normalize_admin_title(getattr(message, "text", None))
            return title if error_key is None else None

        resp, title = await _ask_value(
            client,
            query.message.chat.id,
            "admin_titles.enter_title",
            _parse_title,
            t(_LANG, "admin_titles.invalid"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_USERS,
            prompt_kwargs={"max": str(_ADMIN_TITLE_MAX_LEN)},
        )
        if resp.message is None:
            return
        if not await user_repo.set_owner_admin_title(target_id, title):
            await _send_admin_title_result(
                client,
                query.message.chat.id,
                t(_LANG, "owner_mgmt.not_found"),
                f"{CB['DEV_TITLE_OWNER_LIST_PREFIX']}{page}",
            )
            return
        await _send_admin_title_result(
            client,
            query.message.chat.id,
            t(_LANG, "admin_titles.saved"),
            f"{CB['DEV_TITLE_OWNER_LIST_PREFIX']}{page}",
        )

    @bot.on_callback_query(filters.regex(r"^dev:title:own:clear:-?\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_title_owner_clear(client: Client, query: CallbackQuery):  # noqa: ARG001
        parsed = _parse_admin_title_role_target(query.data, CB["DEV_TITLE_OWNER_CLEAR_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        target_id, page = parsed
        if await user_repo.get_owner(target_id) is None:
            await query.answer(t(_LANG, "owner_mgmt.not_found"), show_alert=True)
            return
        await query.answer()
        issued_at = int(time.time())
        payload = f"{target_id}:{page}:{query.from_user.id}:{issued_at}"
        await query.message.edit_text(
            t(_LANG, "admin_titles.clear_confirm"),
            reply_markup=_admin_title_clear_confirm_kb(
                "owner",
                payload,
                f"{CB['DEV_TITLE_OWNER_LIST_PREFIX']}{page}",
            ),
        )

    @bot.on_callback_query(filters.regex(r"^dev:title:own:clear:do:-?\d+:\d+:\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_title_owner_clear_confirm(client: Client, query: CallbackQuery):  # noqa: ARG001
        parsed = _parse_admin_title_role_clear(query.data, CB["DEV_TITLE_OWNER_CLEAR_DO_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        target_id, page, actor_id, issued_at = parsed
        if query.from_user.id != actor_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_dev_confirm_token(issued_at):
            await query.answer(t(_LANG, "admin_titles.stale_confirm"), show_alert=True)
            return
        if not await user_repo.set_owner_admin_title(target_id, None):
            await query.answer(t(_LANG, "owner_mgmt.not_found"), show_alert=True)
            return
        await query.answer(t(_LANG, "admin_titles.cleared"), show_alert=True)
        await _render_owner_title_list(query, page)

    @bot.on_callback_query(filters.regex(r"^dev:title:own:clear:no:-?\d+:\d+:\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_title_owner_clear_abort(client: Client, query: CallbackQuery):  # noqa: ARG001
        parsed = _parse_admin_title_role_clear(query.data, CB["DEV_TITLE_OWNER_CLEAR_NO_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        _target_id, page, actor_id, issued_at = parsed
        if query.from_user.id != actor_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_dev_confirm_token(issued_at):
            await query.answer(t(_LANG, "admin_titles.stale_confirm"), show_alert=True)
            return
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        await _render_owner_title_list(query, page)

    @bot.on_callback_query(filters.regex(r"^dev:title:own:clear:(?:do|no):.*") & _pm_dev)
    @developer_only
    async def dev_title_owner_clear_malformed(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_LIST_OWNERS']}$") & _pm_dev)
    @developer_only
    async def dev_list_owners(client: Client, query: CallbackQuery):
        await query.answer()
        owners = await user_repo.get_all_owners()
        await _render_role_list(
            query,
            owners,
            "owner_mgmt.list_title",
            "owner_mgmt.list_empty",
            0,
            CB["PAGE_DEV_OWNERS"],
            TOKEN_DEV_USERS,
        )

    @bot.on_callback_query(filters.regex(r"^pg:dev:owners:\d+$") & _pm_dev)
    @developer_only
    async def dev_list_owners_page(client: Client, query: CallbackQuery):
        await query.answer()
        owners = await user_repo.get_all_owners()
        page = int(query.data.split(":")[3])
        await _render_role_list(
            query,
            owners,
            "owner_mgmt.list_title",
            "owner_mgmt.list_empty",
            page,
            CB["PAGE_DEV_OWNERS"],
            TOKEN_DEV_USERS,
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_LIST_SUDOS']}$") & _pm_dev)
    @developer_only
    async def dev_list_sudos(client: Client, query: CallbackQuery):
        await query.answer()
        await _render_sudo_list_page(query, 0)

    @bot.on_callback_query(filters.regex(r"^pg:dev:sudos:\d+$") & _pm_dev)
    @developer_only
    async def dev_list_sudos_page(client: Client, query: CallbackQuery):
        await query.answer()
        page = int(query.data.split(":")[3])
        await _render_sudo_list_page(query, page)

    @bot.on_callback_query(filters.regex(r"^dev:sudo:detail:-?\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_sudo_detail(client: Client, query: CallbackQuery):
        await query.answer()
        parsed = _parse_sudo_detail_payload(query.data, CB["DEV_SUDO_DETAIL_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "sudo_permissions.invalid_callback"), show_alert=True)
            return
        user_id, page = parsed
        await _render_sudo_detail(query, user_id, page)

    @bot.on_callback_query(filters.regex(r"^dev:sudo:back:\d+$") & _pm_dev)
    @developer_only
    async def dev_sudo_list_back(client: Client, query: CallbackQuery):
        await query.answer()
        raw = query.data[len(CB["DEV_SUDO_LIST_BACK_PREFIX"]):]
        try:
            page = max(0, int(raw))
        except ValueError:
            await query.answer(t(_LANG, "sudo_permissions.invalid_callback"), show_alert=True)
            return
        await _render_sudo_list_page(query, page)

    @bot.on_callback_query(filters.regex(r"^dev:title:sudo:set:-?\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_title_sudo_set(client: Client, query: CallbackQuery):
        parsed = _parse_admin_title_role_target(query.data, CB["DEV_TITLE_SUDO_SET_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        target_id, page = parsed
        if await user_repo.get_sudo(target_id) is None:
            await query.answer(t(_LANG, "sudo_mgmt.not_found"), show_alert=True)
            return
        await query.answer()
        def _parse_title(message) -> str | None:
            title, error_key = normalize_admin_title(getattr(message, "text", None))
            return title if error_key is None else None

        resp, title = await _ask_value(
            client,
            query.message.chat.id,
            "admin_titles.enter_title",
            _parse_title,
            t(_LANG, "admin_titles.invalid"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_USERS,
            prompt_kwargs={"max": str(_ADMIN_TITLE_MAX_LEN)},
        )
        if resp.message is None:
            return
        back_callback = f"{CB['DEV_SUDO_DETAIL_PREFIX']}{target_id}:{page}"
        if not await user_repo.set_sudo_admin_title(target_id, title):
            await _send_admin_title_result(
                client,
                query.message.chat.id,
                t(_LANG, "sudo_mgmt.not_found"),
                back_callback,
            )
            return
        await _send_admin_title_result(
            client,
            query.message.chat.id,
            t(_LANG, "admin_titles.saved"),
            back_callback,
        )

    @bot.on_callback_query(filters.regex(r"^dev:title:sudo:clear:-?\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_title_sudo_clear(client: Client, query: CallbackQuery):  # noqa: ARG001
        parsed = _parse_admin_title_role_target(query.data, CB["DEV_TITLE_SUDO_CLEAR_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        target_id, page = parsed
        if await user_repo.get_sudo(target_id) is None:
            await query.answer(t(_LANG, "sudo_mgmt.not_found"), show_alert=True)
            return
        await query.answer()
        issued_at = int(time.time())
        payload = f"{target_id}:{page}:{query.from_user.id}:{issued_at}"
        await query.message.edit_text(
            t(_LANG, "admin_titles.clear_confirm"),
            reply_markup=_admin_title_clear_confirm_kb(
                "sudo",
                payload,
                f"{CB['DEV_SUDO_DETAIL_PREFIX']}{target_id}:{page}",
            ),
        )

    @bot.on_callback_query(filters.regex(r"^dev:title:sudo:clear:do:-?\d+:\d+:\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_title_sudo_clear_confirm(client: Client, query: CallbackQuery):  # noqa: ARG001
        parsed = _parse_admin_title_role_clear(query.data, CB["DEV_TITLE_SUDO_CLEAR_DO_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        target_id, page, actor_id, issued_at = parsed
        if query.from_user.id != actor_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_dev_confirm_token(issued_at):
            await query.answer(t(_LANG, "admin_titles.stale_confirm"), show_alert=True)
            return
        if not await user_repo.set_sudo_admin_title(target_id, None):
            await query.answer(t(_LANG, "sudo_mgmt.not_found"), show_alert=True)
            return
        await query.answer(t(_LANG, "admin_titles.cleared"), show_alert=True)
        await _render_sudo_detail(query, target_id, page)

    @bot.on_callback_query(filters.regex(r"^dev:title:sudo:clear:no:-?\d+:\d+:\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_title_sudo_clear_abort(client: Client, query: CallbackQuery):  # noqa: ARG001
        parsed = _parse_admin_title_role_clear(query.data, CB["DEV_TITLE_SUDO_CLEAR_NO_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        target_id, page, actor_id, issued_at = parsed
        if query.from_user.id != actor_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_dev_confirm_token(issued_at):
            await query.answer(t(_LANG, "admin_titles.stale_confirm"), show_alert=True)
            return
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        await _render_sudo_detail(query, target_id, page)

    @bot.on_callback_query(filters.regex(r"^dev:title:sudo:clear:(?:do|no):.*") & _pm_dev)
    @developer_only
    async def dev_title_sudo_clear_malformed(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)

    @bot.on_callback_query(filters.regex(r"^dev:sp:t:[gcrbsa]:-?\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_sudo_perm_toggle(client: Client, query: CallbackQuery):  # noqa: ARG001
        from app.repositories.user_repo import resolve_sudo_permission_field

        parsed = _parse_sudo_perm_toggle_payload(query.data, CB["DEV_SUDO_PERM_TOGGLE_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "sudo_permissions.invalid_permission"), show_alert=True)
            return
        key, target_uid, page = parsed
        field = resolve_sudo_permission_field(key)
        if field is None:
            await query.answer(t(_LANG, "sudo_permissions.invalid_permission"), show_alert=True)
            return
        sudo = await user_repo.get_sudo_record(target_uid)
        if sudo is None:
            await query.answer(t(_LANG, "sudo_permissions.not_found", user_id=target_uid), show_alert=True)
            return
        perms = user_repo.sudo_permissions_from_row(sudo)
        if perms[field]:
            issued_at = int(time.time())
            await query.answer()
            await query.message.edit_text(
                t(
                    _LANG,
                    "sudo_permissions.disable_confirm_prompt",
                    label=_sudo_perm_field_label(field),
                    user_id=target_uid,
                ),
                reply_markup=_sudo_perm_confirm_kb(
                    key, target_uid, page, query.from_user.id, issued_at
                ),
            )
            return
        ok = await user_repo.set_sudo_permission(target_uid, field, True)
        if not ok:
            await query.answer(t(_LANG, "sudo_permissions.update_failed"), show_alert=True)
            return
        sudo = await user_repo.get_sudo_record(target_uid)
        if sudo is None:
            await query.answer(t(_LANG, "sudo_permissions.not_found", user_id=target_uid), show_alert=True)
            return
        await query.answer(t(_LANG, "sudo_permissions.enabled_notice"), show_alert=True)
        await _render_sudo_detail(query, target_uid, page)

    @bot.on_callback_query(filters.regex(r"^dev:sp:y:[gcrbsa]:-?\d+:\d+:\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_sudo_perm_confirm(client: Client, query: CallbackQuery):  # noqa: ARG001
        from app.repositories.user_repo import resolve_sudo_permission_field

        parsed = _parse_sudo_perm_confirm_bound(query.data, CB["DEV_SUDO_PERM_DO_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "sudo_permissions.invalid_permission"), show_alert=True)
            return
        key, target_uid, page, bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_dev_confirm_token(issued_at):
            await query.answer(t(_LANG, "sudo_permissions.stale_confirm"), show_alert=True)
            return
        field = resolve_sudo_permission_field(key)
        if field is None:
            await query.answer(t(_LANG, "sudo_permissions.invalid_permission"), show_alert=True)
            return
        sudo = await user_repo.get_sudo_record(target_uid)
        if sudo is None:
            await query.answer(t(_LANG, "sudo_permissions.not_found", user_id=target_uid), show_alert=True)
            return
        ok = await user_repo.set_sudo_permission(target_uid, field, False)
        if not ok:
            await query.answer(t(_LANG, "sudo_permissions.update_failed"), show_alert=True)
            return
        await query.answer(t(_LANG, "sudo_permissions.disabled_notice"), show_alert=True)
        await _render_sudo_detail(query, target_uid, page)

    @bot.on_callback_query(filters.regex(r"^dev:sp:n:[gcrbsa]:-?\d+:\d+:\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_sudo_perm_abort(client: Client, query: CallbackQuery):  # noqa: ARG001
        parsed = _parse_sudo_perm_confirm_bound(query.data, CB["DEV_SUDO_PERM_NO_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "sudo_permissions.invalid_permission"), show_alert=True)
            return
        _key, target_uid, page, bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_dev_confirm_token(issued_at):
            await query.answer(t(_LANG, "sudo_permissions.stale_confirm"), show_alert=True)
            return
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        await _render_sudo_detail(query, target_uid, page)

    @bot.on_callback_query(filters.regex(r"^dev:sp:(?:t|y|n):.*") & _pm_dev)
    @developer_only
    async def dev_sudo_perm_malformed(client: Client, query: CallbackQuery):  # noqa: ARG001
        for prefix, parser in (
            (CB["DEV_SUDO_PERM_TOGGLE_PREFIX"], _parse_sudo_perm_toggle_payload),
            (CB["DEV_SUDO_PERM_DO_PREFIX"], _parse_sudo_perm_confirm_bound),
            (CB["DEV_SUDO_PERM_NO_PREFIX"], _parse_sudo_perm_confirm_bound),
        ):
            if parser(query.data, prefix) is not None:
                return
        await query.answer(t(_LANG, "sudo_permissions.invalid_permission"), show_alert=True)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_REMOVE_OWNER']}$") & _pm_dev)
    @developer_only
    async def dev_remove_owner(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        resp, uid = await _ask_value(
            client,
            chat_id,
            "ask.user_id",
            lambda message: parse_user_id(getattr(message, "text", None)),
            t(_LANG, "common.errors.invalid_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_USERS,
        )
        if resp.message is None:
            return
        owner = await user_repo.get_owner(uid)
        if owner is None:
            await _send_done(client, chat_id, t(_LANG, "owner_mgmt.not_found"), TOKEN_DEV_USERS)
            return
        issued_at = int(time.time())
        await deliver_ask_outcome(
            client,
            chat_id,
            query.from_user.id,
            t(_LANG, "owner_mgmt.remove_confirm_prompt", user=str(uid)),
            KeyboardFactory.dev_privileged_user_remove_confirm(
                _LANG, "owner", uid, query.from_user.id, issued_at
            ),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_REMOVE_SUDO']}$") & _pm_dev)
    @developer_only
    async def dev_remove_sudo(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        resp, uid = await _ask_value(
            client,
            chat_id,
            "ask.user_id",
            lambda message: parse_user_id(getattr(message, "text", None)),
            t(_LANG, "common.errors.invalid_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_USERS,
        )
        if resp.message is None:
            return
        sudo = await user_repo.get_sudo(uid)
        if sudo is None:
            await _send_done(client, chat_id, t(_LANG, "sudo_mgmt.not_found"), TOKEN_DEV_USERS)
            return
        issued_at = int(time.time())
        await deliver_ask_outcome(
            client,
            chat_id,
            query.from_user.id,
            t(_LANG, "sudo_mgmt.remove_confirm_prompt", user=str(uid)),
            KeyboardFactory.dev_privileged_user_remove_confirm(
                _LANG, "sudo", uid, query.from_user.id, issued_at
            ),
        )

    @bot.on_callback_query(filters.regex(r"^dev:owner:rm:do:-?\d+:\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_owner_remove_confirm(client: Client, query: CallbackQuery):
        parsed = _parse_privileged_remove_bound(query.data, CB["DEV_OWNER_REMOVE_EXEC_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        target_uid, bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_dev_confirm_token(issued_at):
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        owner = await user_repo.get_owner(target_uid)
        if owner is None:
            await query.answer()
            await _send_done(client, query.message.chat.id, t(_LANG, "owner_mgmt.not_found"), TOKEN_DEV_USERS)
            return
        await query.answer()
        await user_repo.remove_owner(target_uid)
        await invalidate_ownerlist()
        try:
            await NotificationService.notify_owner_removed(
                client, target_uid, query.from_user.id
            )
        except Exception:
            logger.debug("notify_owner_removed failed", exc_info=True)
        await _send_done(
            client, query.message.chat.id, t(_LANG, "owner_mgmt.removed", user=str(target_uid)), TOKEN_DEV_USERS
        )

    @bot.on_callback_query(filters.regex(r"^dev:owner:rm:no:\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_owner_remove_abort(client: Client, query: CallbackQuery):
        parsed = _parse_privileged_remove_abort(query.data, CB["DEV_OWNER_REMOVE_ABORT_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_dev_confirm_token(issued_at):
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        await _send_done(client, query.message.chat.id, t(_LANG, "common.cancelled"), TOKEN_DEV_USERS)

    @bot.on_callback_query(filters.regex(r"^dev:sudo:rm:do:-?\d+:\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_sudo_remove_confirm(client: Client, query: CallbackQuery):
        parsed = _parse_privileged_remove_bound(query.data, CB["DEV_SUDO_REMOVE_EXEC_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        target_uid, bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_dev_confirm_token(issued_at):
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        sudo = await user_repo.get_sudo(target_uid)
        if sudo is None:
            await query.answer()
            await _send_done(client, query.message.chat.id, t(_LANG, "sudo_mgmt.not_found"), TOKEN_DEV_USERS)
            return
        await query.answer()
        await user_repo.remove_sudo(target_uid)
        await invalidate_sudolist()
        try:
            await NotificationService.notify_sudo_removed(
                client, target_uid, query.from_user.id
            )
        except Exception:
            logger.debug("notify_sudo_removed failed", exc_info=True)
        await _send_done(
            client, query.message.chat.id, t(_LANG, "sudo_mgmt.removed", user=str(target_uid)), TOKEN_DEV_USERS
        )

    @bot.on_callback_query(filters.regex(r"^dev:sudo:rm:no:\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_sudo_remove_abort(client: Client, query: CallbackQuery):
        parsed = _parse_privileged_remove_abort(query.data, CB["DEV_SUDO_REMOVE_ABORT_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_dev_confirm_token(issued_at):
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        await _send_done(client, query.message.chat.id, t(_LANG, "common.cancelled"), TOKEN_DEV_USERS)

    @bot.on_callback_query(filters.regex(r"^dev:(?:owner|sudo):rm:(?:do|no):.*") & _pm_dev)
    @developer_only
    async def dev_privileged_remove_malformed(client: Client, query: CallbackQuery):  # noqa: ARG001
        for prefix in (
            CB["DEV_OWNER_REMOVE_EXEC_PREFIX"],
            CB["DEV_OWNER_REMOVE_ABORT_PREFIX"],
            CB["DEV_SUDO_REMOVE_EXEC_PREFIX"],
            CB["DEV_SUDO_REMOVE_ABORT_PREFIX"],
        ):
            if prefix.endswith(":no:"):
                if _parse_privileged_remove_abort(query.data, prefix) is not None:
                    return
            elif _parse_privileged_remove_bound(query.data, prefix) is not None:
                return
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_CAT_TEXTS']}$") & _pm_dev)
    @developer_only
    async def dev_cat_texts(client: Client, query: CallbackQuery):
        await query.answer()
        await query.message.edit_text(
            await _build_dev_texts_text(),
            reply_markup=KeyboardFactory.dev_sub_texts(_LANG),
        )

    # ?? Developer texts & links ???????????????????????????????????????????
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_TEXTS_BACK']}$") & _pm_dev)
    @developer_only
    async def dev_texts_back(client: Client, query: CallbackQuery):
        await query.answer()
        await query.message.edit_text(
            await _build_dev_texts_text(),
            reply_markup=KeyboardFactory.dev_sub_texts(_LANG),
        )

    # Identity-only filters (panel root): deny-capable filters skip the handler
    # on stale taps; role via @developer_only, scope answered explicitly.
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_TEXTS_HOME']}$"))
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_TEXTS_LINKS']}$"))
    @developer_only
    async def dev_texts_home(client: Client, query: CallbackQuery):
        chat = query.message.chat if query.message else None
        if chat is None or getattr(chat.type, "value", chat.type) != "private":
            await query.answer(
                t(_LANG, "common.errors.unknown_callback"), show_alert=True
            )
            return
        await query.answer()
        text, kb = await build_texts_hub_payload(_LANG, "dev")
        await query.message.edit_text(text, reply_markup=kb)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_START_STYLE_TOGGLE']}$") & _pm_dev)
    @developer_only
    async def dev_start_style_toggle(client: Client, query: CallbackQuery):  # noqa: ARG001
        updated = await start_custom.toggle_style_mode(
            scope_type="global",
            owner_user_id=None,
            actor_user_id=query.from_user.id,
        )
        next_mode = updated.mode
        text, kb = await build_texts_hub_payload(_LANG, "dev")
        await query.message.edit_text(text, reply_markup=kb)
        await query.answer(
            t(
                _LANG,
                "texts_links.start_style.saved_global",
                mode=t(_LANG, f"texts_links.start_style.mode_{next_mode}"),
            ),
            show_alert=False,
        )

    @bot.on_callback_query(filters.regex(r"^dev:text:f:[a-z0-9_]+$") & _pm_dev)
    @developer_only
    async def dev_text_field(client: Client, query: CallbackQuery):
        await query.answer()
        field = field_from_callback(query.data, CB["DEV_TEXT_FIELD_PREFIX"])
        if field is None:
            await query.answer(t(_LANG, "common.errors.try_later"), show_alert=True)
            return
        text, kb = await build_text_field_payload(_LANG, "dev", field)
        await query.message.edit_text(text, reply_markup=kb)

    @bot.on_callback_query(filters.regex(r"^dev:text:txt:[a-z0-9_]+$") & _pm_dev)
    @developer_only
    async def dev_text_set_text(client: Client, query: CallbackQuery):
        await query.answer()
        field = field_from_callback(query.data, CB["DEV_TEXT_SET_TEXT_PREFIX"])
        if field is None:
            await query.answer(t(_LANG, "common.errors.try_later"), show_alert=True)
            return

        spec = get_field_spec(field)
        if spec is None:
            await query.answer(t(_LANG, "common.errors.try_later"), show_alert=True)
            return

        chat_id = query.message.chat.id

        def _parse_text_value(message) -> str | None:
            value = (getattr(message, "text", "") or "").strip()
            if not value:
                return None
            if spec.kind == "link":
                value = normalize_link_value(value)
                if not is_valid_link_value(value):
                    return None
            return value

        invalid_key = (
            "texts_links.invalid_link"
            if spec.kind == "link"
            else "texts_links.invalid_text"
        )
        resp, value = await _ask_value(
            client,
            chat_id,
            "texts_links.prompt_set_text",
            _parse_text_value,
            t(_LANG, invalid_key),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_TEXTS,
            prompt_kwargs={"field": t(_LANG, spec.label_key)},
        )
        if await _notify_texts_links_ask_abort(client, chat_id, resp, TOKEN_DEV_TEXTS):
            return

        await settings_repo.set_bot_setting(field, value, updated_by=query.from_user.id)
        text, kb = await build_text_field_payload(_LANG, "dev", field)
        await deliver_ask_outcome(
            client,
            chat_id,
            query.from_user.id,
            f"{t(_LANG, confirmation_key_for_save(spec))}\n\n{text}",
            kb,
            query_message=query.message,
        )

    @bot.on_callback_query(filters.regex(r"^dev:text:med:[a-z0-9_]+$") & _pm_dev)
    @developer_only
    async def dev_text_set_media(client: Client, query: CallbackQuery):
        await query.answer()
        field = field_from_callback(query.data, CB["DEV_TEXT_SET_MEDIA_PREFIX"])
        if field is None:
            await query.answer(t(_LANG, "common.errors.try_later"), show_alert=True)
            return

        spec = get_field_spec(field)
        if spec is None:
            await query.answer(t(_LANG, "common.errors.try_later"), show_alert=True)
            return

        chat_id = query.message.chat.id
        retry_prompt: str | None = None
        while True:
            resp = await _ask(
                client,
                chat_id,
                "texts_links.prompt_set_media",
                user_id=query.from_user.id,
                return_to=TOKEN_DEV_TEXTS,
                prompt_kwargs={"field": t(_LANG, spec.label_key)},
                prompt_text=retry_prompt,
                delete_response=False,
            )
            if await _notify_texts_links_ask_abort(client, chat_id, resp, TOKEN_DEV_TEXTS):
                return

            file_id, caption = extract_media_payload(resp.message)
            if file_id:
                await settings_repo.set_bot_setting(
                    field,
                    encode_media_value(file_id, caption),
                    updated_by=query.from_user.id,
                )
                text, kb = await build_text_field_payload(_LANG, "dev", field)
                await deliver_ask_outcome(
                    client,
                    chat_id,
                    query.from_user.id,
                    f"{t(_LANG, confirmation_key_for_save(spec, media=True))}\n\n{text}",
                    kb,
                    query_message=query.message,
                )
                return

            await safe_delete_user_input(resp.message)
            retry_prompt = (
                f"{t(_LANG, 'texts_links.invalid_media')}\n\n"
                f"{t(_LANG, 'texts_links.prompt_set_media', field=t(_LANG, spec.label_key))}"
            )

    @bot.on_callback_query(filters.regex(r"^dev:text:clr:[a-z0-9_]+$") & _pm_dev)
    @developer_only
    async def dev_text_clear(client: Client, query: CallbackQuery):
        await query.answer()
        field = field_from_callback(query.data, CB["DEV_TEXT_CLEAR_PREFIX"])
        if field is None:
            await query.answer(t(_LANG, "common.errors.try_later"), show_alert=True)
            return
        spec = get_field_spec(field)
        issued_at = int(time.time())
        await query.message.edit_text(
            t(_LANG, "texts_links.clear_confirm_prompt", field=t(_LANG, spec.label_key)),
            reply_markup=KeyboardFactory.dev_text_clear_confirm(
                _LANG, field, query.from_user.id, issued_at
            ),
        )

    @bot.on_callback_query(filters.regex(r"^dev:text:clr:do:[a-z0-9_]+:\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_text_clear_confirm(client: Client, query: CallbackQuery):
        parsed = _parse_text_clear_bound(query.data, CB["DEV_TEXT_CLEAR_EXEC_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        field, bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_dev_confirm_token(issued_at):
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer()
        await settings_repo.set_bot_setting(field, None, updated_by=query.from_user.id)
        text, kb = await build_text_field_payload(_LANG, "dev", field)
        await query.message.edit_text(text, reply_markup=kb)
        await _send_done(
            client,
            query.message.chat.id,
            t(_LANG, confirmation_key_for_clear()),
            TOKEN_DEV_TEXTS,
        )

    @bot.on_callback_query(filters.regex(r"^dev:text:clr:no:[a-z0-9_]+:\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_text_clear_abort(client: Client, query: CallbackQuery):
        parsed = _parse_text_clear_bound(query.data, CB["DEV_TEXT_CLEAR_ABORT_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        field, bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_dev_confirm_token(issued_at):
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        text, kb = await build_text_field_payload(_LANG, "dev", field)
        await query.message.edit_text(text, reply_markup=kb)

    @bot.on_callback_query(filters.regex(r"^dev:text:clr:(?:do|no):.*") & _pm_dev)
    @developer_only
    async def dev_text_clear_malformed(client: Client, query: CallbackQuery):  # noqa: ARG001
        if _parse_text_clear_bound(query.data, CB["DEV_TEXT_CLEAR_EXEC_PREFIX"]) is not None:
            return
        if _parse_text_clear_bound(query.data, CB["DEV_TEXT_CLEAR_ABORT_PREFIX"]) is not None:
            return
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)

    @bot.on_callback_query(filters.regex(r"^dev:text:prv:[a-z0-9_]+$") & _pm_dev)
    @developer_only
    async def dev_text_preview(client: Client, query: CallbackQuery):
        await query.answer()
        field = field_from_callback(query.data, CB["DEV_TEXT_PREVIEW_PREFIX"])
        if field is None:
            await query.answer(t(_LANG, "common.errors.try_later"), show_alert=True)
            return

        raw = await settings_repo.get_bot_setting(field)
        parsed_value = decode_setting_value(raw)
        if parsed_value.get("mode") != "media":
            await query.answer(t(_LANG, "texts_links.preview_unavailable"), show_alert=True)
            return

        await replace_panel_with_photo(
            client,
            query,
            parsed_value.get("file_id"),
            caption=parsed_value.get("caption") or None,
            reply_markup=build_done_kb(_LANG, TOKEN_DEV_TEXTS),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_INCREASE_CREDIT']}$") & _pm_dev)
    @developer_only
    async def dev_increase_credit(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        resp, target_id = await _ask_value(
            client,
            chat_id,
            "ask.chat_id",
            lambda message: parse_user_id(getattr(message, "text", None)),
            t(_LANG, "common.errors.invalid_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_CREDIT,
        )
        if await notify_ask_abort(client, chat_id, resp, return_to=TOKEN_DEV_CREDIT, lang=_LANG):
            return
        resp2, days = await _ask_value(
            client,
            chat_id,
            "ask.amount_days",
            lambda message: parse_bounded_int(
                getattr(message, "text", None),
                min_value=1,
                max_value=MAX_CREDIT_DAYS,
            ),
            t(_LANG, "common.errors.invalid_positive_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_CREDIT,
        )
        if await notify_ask_abort(client, chat_id, resp2, return_to=TOKEN_DEV_CREDIT, lang=_LANG):
            return
        try:
            await CreditService.charge_managed_chat(
                target_id,
                "group",
                days,
                operated_by=query.from_user.id,
                note="developer_manual_credit",
            )
        except ValueError as exc:
            if "not_managed" in str(exc):
                await _send_done(client, chat_id, t(_LANG, "credit.charge_group_not_managed"), TOKEN_DEV_CREDIT)
                return
            raise
        await _send_done(
            client,
            chat_id,
            t(_LANG, "credit.charged", amount=days, chat_title=str(target_id)),
            TOKEN_DEV_CREDIT,
        )

    # ── Credit decrease ───────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_DECREASE_CREDIT']}$") & _pm_dev)
    @developer_only
    async def dev_decrease_credit(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        resp, target_id = await _ask_value(
            client,
            chat_id,
            "ask.chat_id",
            lambda message: parse_user_id(getattr(message, "text", None)),
            t(_LANG, "common.errors.invalid_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_CREDIT,
        )
        if await notify_ask_abort(client, chat_id, resp, return_to=TOKEN_DEV_CREDIT, lang=_LANG):
            return
        resp2, days = await _ask_value(
            client,
            chat_id,
            "ask.amount_days",
            lambda message: parse_bounded_int(
                getattr(message, "text", None),
                min_value=1,
                max_value=MAX_CREDIT_DAYS,
            ),
            t(_LANG, "common.errors.invalid_positive_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_CREDIT,
        )
        if await notify_ask_abort(client, chat_id, resp2, return_to=TOKEN_DEV_CREDIT, lang=_LANG):
            return
        try:
            await CreditService.adjust_managed_credit(
                target_id,
                "group",
                mode="decrease",
                amount=days,
                operated_by=query.from_user.id,
                note="developer_manual_credit",
            )
        except ValueError as exc:
            if "not_managed" in str(exc):
                await _send_done(client, chat_id, t(_LANG, "credit.charge_group_not_managed"), TOKEN_DEV_CREDIT)
                return
            raise
        await _send_done(
            client,
            chat_id,
            t(_LANG, "credit.deducted", amount=days, chat_title=str(target_id)),
            TOKEN_DEV_CREDIT,
        )

    # ── Rate setters ──────────────────────────────────────────────────────
    # ?? Invoice management ?????????????????????????????????????????????????
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_SEND_INVOICE']}$") & _pm_dev)
    @developer_only
    async def dev_send_invoice(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id

        target_resp, target_id = await _ask_value(
            client,
            chat_id,
            "ask.chat_id",
            lambda message: parse_user_id(getattr(message, "text", None)),
            t(_LANG, "common.errors.invalid_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_CREDIT,
        )
        if await notify_ask_abort(client, chat_id, target_resp, return_to=TOKEN_DEV_CREDIT, lang=_LANG):
            return

        days_resp, days = await _ask_value(
            client,
            chat_id,
            "ask.amount_days",
            lambda message: parse_bounded_int(
                getattr(message, "text", None),
                min_value=1,
                max_value=MAX_CREDIT_DAYS,
            ),
            t(_LANG, "common.errors.invalid_positive_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_CREDIT,
        )
        if await notify_ask_abort(client, chat_id, days_resp, return_to=TOKEN_DEV_CREDIT, lang=_LANG):
            return

        rate_raw = await settings_repo.get_bot_setting("base_rate")
        try:
            base_rate = int(rate_raw) if rate_raw else 0
        except ValueError:
            base_rate = 0

        amount = max(0, days * base_rate)

        from app.database.engine import async_session
        from app.database.models import Invoice

        async with async_session() as session:
            async with session.begin():
                inv = Invoice(
                    chat_id=target_id,
                    chat_type="group" if target_id < 0 else "private",
                    amount=amount,
                    days=days,
                    issued_by=query.from_user.id,
                    status="pending",
                )
                session.add(inv)
            await session.refresh(inv)

        await AdminDashboardService.invalidate_cache("credit")
        await _send_done(
            client,
            chat_id,
            t(_LANG, "status.invoice_created", invoice_id=inv.id),
            TOKEN_DEV_CREDIT,
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_INVOICE_HISTORY']}$") & _pm_dev)
    @developer_only
    async def dev_invoice_history(client: Client, query: CallbackQuery):
        await query.answer()

        from app.database.engine import async_session
        from app.database.models import Invoice
        from sqlalchemy import select

        async with async_session() as session:
            stmt = select(Invoice).order_by(Invoice.issued_at.desc()).limit(20)
            result = await session.execute(stmt)
            invoices = list(result.scalars().all())

        if not invoices:
            await query.message.edit_text(
                t(_LANG, "panels.developer.invoice_history_empty"),
                reply_markup=build_done_kb(_LANG, TOKEN_DEV_CREDIT),
            )
            return

        lines = [t(_LANG, "panels.developer.invoice_history_title")]
        for inv in invoices:
            date_text = inv.issued_at.strftime("%Y-%m-%d") if inv.issued_at else "-"
            lines.append(
                t(
                    _LANG,
                    "panels.developer.invoice_history_item",
                    id=inv.id,
                    date=date_text,
                    days=inv.days,
                    status=label(_LANG, "invoice_status", inv.status),
                )
            )

        await query.message.edit_text(
            "\n".join(lines),
            reply_markup=build_done_kb(_LANG, TOKEN_DEV_CREDIT),
        )

    async def _set_rate(
        client: Client,
        query: CallbackQuery,
        setting_key: str,
        *,
        return_to: str = TOKEN_DEV_RATES,
    ):

        await query.answer()
        await remember_panel_from_query(query)
        chat_id = query.message.chat.id
        user_id = query.from_user.id
        resp, value = await _ask_value(
            client,
            chat_id,
            "ask.rate_value",
            lambda message: parse_bounded_int(
                getattr(message, "text", None),
                min_value=0,
                max_value=MAX_RATE_VALUE,
            ),
            t(_LANG, "common.errors.invalid_non_negative_number"),
            user_id=user_id,
            return_to=return_to,
        )
        if await notify_ask_abort(
            client, chat_id, resp, return_to=return_to, lang=_LANG, user_id=user_id,
        ):
            return
        await settings_repo.set_bot_setting(setting_key, str(value), updated_by=user_id)
        if setting_key == "call_security_rate":
            await settings_repo.set_bot_setting(
                "security_call_rate", str(value), updated_by=user_id,
            )
        await _send_done(
            client,
            chat_id,
            t(_LANG, "status.rate_updated", value=value),
            return_to,
            user_id=user_id,
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_SET_BASE_RATE']}$") & _pm_dev)
    @developer_only
    async def dev_set_base_rate(client: Client, query: CallbackQuery):
        await _set_rate(client, query, "base_rate")

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_SET_MUSIC_RATE']}$") & _pm_dev)
    @developer_only
    async def dev_set_music_rate(client: Client, query: CallbackQuery):
        await _set_rate(client, query, "music_rate")

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_SET_VIDEO_RATE']}$") & _pm_dev)
    @developer_only
    async def dev_set_video_rate(client: Client, query: CallbackQuery):
        await _set_rate(client, query, "video_rate")

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_SET_CALL_SECURITY_RATE']}$") & _pm_dev)
    @developer_only
    async def dev_set_call_security_rate(client: Client, query: CallbackQuery):
        await _set_rate(client, query, "call_security_rate")

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_SET_MUSIC_SELL_RATE']}$") & _pm_dev)
    @developer_only
    async def dev_set_music_sell_rate(client: Client, query: CallbackQuery):
        await _set_rate(client, query, "music_sell_rate")

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_SET_VIDEO_SELL_RATE']}$") & _pm_dev)
    @developer_only
    async def dev_set_video_sell_rate(client: Client, query: CallbackQuery):
        await _set_rate(client, query, "video_sell_rate")

    # ── Broadcast / Forward (legacy → advanced wizard) ────────────────────
    async def _redirect_legacy_broadcast(client: Client, query: CallbackQuery) -> None:
        await query.answer()
        await begin_broadcast_wizard(client, query, legacy_notice=True)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_BROADCAST_GROUP']}$") & _pm_dev)
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_FORWARD_GROUP']}$") & _pm_dev)
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_BROADCAST_PRIVATE']}$") & _pm_dev)
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_FORWARD_PRIVATE']}$") & _pm_dev)
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_BROADCAST_CHANNEL']}$") & _pm_dev)
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_FORWARD_CHANNEL']}$") & _pm_dev)
    @developer_only
    async def dev_legacy_broadcast_to_wizard(client: Client, query: CallbackQuery):
        await _redirect_legacy_broadcast(client, query)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_SEND_TO_SUDO']}$") & _pm_dev)
    @developer_only
    async def dev_send_to_sudo(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id

        uid_resp, sudo_id = await _ask_value(
            client,
            chat_id,
            "sudo_mgmt.send_to_sudo_prompt",
            lambda message: parse_user_id(getattr(message, "text", None)),
            t(_LANG, "common.errors.invalid_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_BROADCAST,
        )
        if uid_resp.message is None:
            return

        msg_resp, text = await _ask_value(
            client,
            chat_id,
            "ask.broadcast_msg",
            lambda message: (getattr(message, "text", "") or "").strip() or None,
            t(_LANG, "texts_links.invalid_text"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_BROADCAST,
        )
        if msg_resp.message is None:
            return

        from app.utils.safe_sender import safe_send_message

        await safe_send_message(client, sudo_id, text)
        await _send_done(client, chat_id, t(_LANG, "sudo_mgmt.message_sent"), TOKEN_DEV_BROADCAST)

    # ── Force join toggle ─────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_FORCE_JOIN_TOGGLE']}$") & _pm_dev)
    @developer_only
    async def dev_force_join_toggle(client: Client, query: CallbackQuery):
        await _toggle_dev_setting(query, "force_join_enabled", "status.force_join_label")
    # ── Deprecated global channel-security toggle ─────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_CHANNEL_SECURITY_TOGGLE']}$") & _pm_dev)
    @developer_only
    async def dev_channel_security_toggle(client: Client, query: CallbackQuery):
        await query.answer()
        await query.message.edit_text(
            t(_LANG, "call_security.deprecated_global_toggle"),
            reply_markup=await _dev_settings_keyboard(),
        )

    # ── Auto-leave toggle ─────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_AUTO_LEAVE_TOGGLE']}$") & _pm_dev)
    @developer_only
    async def dev_auto_leave_toggle(client: Client, query: CallbackQuery):
        await _toggle_dev_setting(query, "auto_leave_enabled", "status.auto_leave_label")

    # ── Trial toggle ──────────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_TRIAL_TOGGLE']}$") & _pm_dev)
    @developer_only
    async def dev_trial_toggle(client: Client, query: CallbackQuery):
        await _toggle_dev_setting(query, "trial_enabled", "status.trial_label")

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_BOT_ENABLED_TOGGLE']}$") & _pm_dev)
    @developer_only
    async def dev_bot_enabled_toggle(client: Client, query: CallbackQuery):
        await _toggle_dev_setting(query, "bot_enabled", "status.bot_enabled_label", default=True)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_SUDO_PANEL_ENABLED_TOGGLE']}$") & _pm_dev)
    @developer_only
    async def dev_sudo_panel_enabled_toggle(client: Client, query: CallbackQuery):
        await _toggle_dev_setting(
            query,
            "sudo_panel_enabled",
            "status.sudo_panel_enabled_label",
            default=True,
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_BOT_UPDATE']}$") & _pm_dev)
    @developer_only
    async def dev_bot_update(client: Client, query: CallbackQuery):
        await query.answer()
        await _render_bot_update_panel(query)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_BOT_UPDATE_REFRESH']}$") & _pm_dev)
    @developer_only
    async def dev_bot_update_refresh(client: Client, query: CallbackQuery):
        await query.answer()
        await _render_bot_update_panel(query)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_BOT_UPDATE_RELOAD']}$") & _pm_dev)
    @developer_only
    async def dev_bot_update_reload_prompt(client: Client, query: CallbackQuery):
        await query.answer()
        if not reload_is_available():
            await query.answer(t(_LANG, "bot_update.reload_not_configured"), show_alert=True)
            return
        issued_at = int(time.time())
        await query.message.edit_text(
            t(_LANG, "bot_update.reload_confirm_prompt"),
            reply_markup=KeyboardFactory.dev_bot_update_reload_confirm(
                _LANG,
                query.from_user.id,
                issued_at,
            ),
        )

    @bot.on_callback_query(
        filters.regex(r"^dev:bot_update:reload:do:\d+:\d+$") & _pm_dev
    )
    @developer_only
    async def dev_bot_update_reload_confirm(client: Client, query: CallbackQuery):
        parsed = _parse_media_cleanup_bound(query.data, CB["DEV_BOT_UPDATE_RELOAD_EXEC_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "bot_update.reload_wrong_user"), show_alert=True)
            return
        if _is_stale_dev_confirm_token(issued_at):
            await query.answer(t(_LANG, "bot_update.reload_stale_confirm"), show_alert=True)
            return
        await query.answer()
        result = await execute_configured_reload(query.from_user.id)
        if result.ok:
            if result.method == "sentinel":
                text = t(_LANG, "bot_update.reload_submitted")
            else:
                text = t(_LANG, "bot_update.reload_submitted")
            if result.detail and result.method == "command":
                text += "\n" + t(_LANG, "bot_update.reload_result_line", detail=result.detail[:180])
        else:
            text = t(
                _LANG,
                "bot_update.reload_failed",
                detail=result.detail[:180] if result.detail else t(_LANG, "bot_update.status_unknown"),
            )
        await query.message.edit_text(
            text,
            reply_markup=KeyboardFactory.dev_bot_update(
                _LANG,
                reload_available=reload_is_available(),
            ),
        )

    @bot.on_callback_query(
        filters.regex(r"^dev:bot_update:reload:no:\d+:\d+$") & _pm_dev
    )
    @developer_only
    async def dev_bot_update_reload_cancel(client: Client, query: CallbackQuery):
        parsed = _parse_media_cleanup_bound(query.data, CB["DEV_BOT_UPDATE_RELOAD_ABORT_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "bot_update.reload_wrong_user"), show_alert=True)
            return
        if _is_stale_dev_confirm_token(issued_at):
            await query.answer(t(_LANG, "bot_update.reload_stale_confirm"), show_alert=True)
            return
        await query.answer(t(_LANG, "bot_update.reload_cancelled"), show_alert=False)
        await _render_bot_update_panel(query)

    @bot.on_callback_query(filters.regex(r"^dev:bot_update:reload:(?:do|no):.*") & _pm_dev)
    @developer_only
    async def dev_bot_update_reload_malformed(client: Client, query: CallbackQuery):  # noqa: ARG001
        if _parse_media_cleanup_bound(query.data, CB["DEV_BOT_UPDATE_RELOAD_EXEC_PREFIX"]) is not None:
            return
        if _parse_media_cleanup_bound(query.data, CB["DEV_BOT_UPDATE_RELOAD_ABORT_PREFIX"]) is not None:
            return
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_MEDIA_HEALTH']}$") & _pm_dev)
    @developer_only
    async def dev_media_health(client: Client, query: CallbackQuery):
        await query.answer()
        await _render_media_health_panel(query)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_MEDIA_CLEANUP_PREVIEW']}$") & _pm_dev)
    @developer_only
    async def dev_media_cleanup_preview(client: Client, query: CallbackQuery):
        await query.answer()
        try:
            preview = await build_cleanup_preview()
            text = format_cleanup_preview(_LANG, preview)
            if preview.cache_count == 0 and preview.downloads_count == 0:
                await query.message.edit_text(
                    text,
                    reply_markup=KeyboardFactory.dev_media_health(_LANG),
                )
                return
            issued_at = int(time.time())
            await query.message.edit_text(
                text,
                reply_markup=KeyboardFactory.dev_media_cleanup_confirm(
                    _LANG,
                    query.from_user.id,
                    issued_at,
                ),
            )
        except Exception:
            logger.exception("Developer media cleanup preview failed")
            await query.message.edit_text(
                t(_LANG, "media_health.cleanup_failed"),
                reply_markup=KeyboardFactory.dev_media_health(_LANG),
            )

    @bot.on_callback_query(
        filters.regex(r"^dev:media:cleanup:do:\d+:\d+$") & _pm_dev
    )
    @developer_only
    async def dev_media_cleanup_confirm(client: Client, query: CallbackQuery):
        parsed = _parse_media_cleanup_bound(query.data, CB["DEV_MEDIA_CLEANUP_EXEC_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "media_health.cleanup_wrong_user"), show_alert=True)
            return
        if _is_stale_dev_confirm_token(issued_at):
            await query.answer(t(_LANG, "media_health.cleanup_stale_confirm"), show_alert=True)
            return
        await query.answer()
        try:
            result = await execute_safe_cleanup()
            await query.message.edit_text(
                format_cleanup_result(_LANG, result),
                reply_markup=KeyboardFactory.dev_media_health(_LANG),
            )
        except Exception:
            logger.exception("Developer media safe cleanup failed")
            await query.message.edit_text(
                t(_LANG, "media_health.cleanup_failed"),
                reply_markup=KeyboardFactory.dev_media_health(_LANG),
            )

    @bot.on_callback_query(
        filters.regex(r"^dev:media:cleanup:no:\d+:\d+$") & _pm_dev
    )
    @developer_only
    async def dev_media_cleanup_cancel(client: Client, query: CallbackQuery):
        parsed = _parse_media_cleanup_bound(query.data, CB["DEV_MEDIA_CLEANUP_ABORT_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "media_health.cleanup_wrong_user"), show_alert=True)
            return
        if _is_stale_dev_confirm_token(issued_at):
            await query.answer(t(_LANG, "media_health.cleanup_stale_confirm"), show_alert=True)
            return
        await query.answer(t(_LANG, "media_health.cleanup_cancelled"), show_alert=False)
        await _render_media_health_panel(query)

    @bot.on_callback_query(filters.regex(r"^dev:media:cleanup:(?:do|no):.*") & _pm_dev)
    @developer_only
    async def dev_media_cleanup_malformed(client: Client, query: CallbackQuery):  # noqa: ARG001
        if _parse_media_cleanup_bound(query.data, CB["DEV_MEDIA_CLEANUP_EXEC_PREFIX"]) is not None:
            return
        if _parse_media_cleanup_bound(query.data, CB["DEV_MEDIA_CLEANUP_ABORT_PREFIX"]) is not None:
            return
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_MEDIA_RANKING']}$") & _pm_dev)
    @developer_only
    async def dev_media_ranking(client: Client, query: CallbackQuery):
        await query.answer()
        try:
            summary = await get_media_ranking_summary()
            text = format_media_ranking_report(_LANG, summary)
            await query.message.edit_text(
                text,
                reply_markup=KeyboardFactory.dev_media_health(_LANG),
            )
        except Exception:
            logger.exception("Developer media ranking panel failed")
            await query.message.edit_text(
                t(_LANG, "media_health.ranking_error"),
                reply_markup=KeyboardFactory.dev_media_health(_LANG),
            )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_MEDIA_EXPORT']}$") & _pm_dev)
    @developer_only
    async def dev_media_export(client: Client, query: CallbackQuery):
        await query.answer()
        temp_path = None
        try:
            report = await build_media_report()
            temp_path, filename = write_media_report_tempfile(report)
            await client.send_document(
                query.message.chat.id,
                str(temp_path),
                file_name=filename,
            )
            await query.message.edit_text(
                t(_LANG, "media_health.export_sent"),
                reply_markup=KeyboardFactory.dev_media_health(_LANG),
            )
        except Exception:
            logger.exception("Developer media JSON export failed")
            await query.message.edit_text(
                t(_LANG, "media_health.export_failed"),
                reply_markup=KeyboardFactory.dev_media_health(_LANG),
            )
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_SET_MEDIA_POLICY']}$") & _pm_dev)
    @developer_only
    async def dev_set_media_policy(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        resp, value = await _ask_value(
            client,
            chat_id,
            "ask.text_input",
            lambda message: (getattr(message, "text", "") or "").strip() or None,
            t(_LANG, "common.errors.invalid_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_SETTINGS,
        )
        if resp.message is None:
            return

        await settings_repo.set_bot_setting("media_policy", value, updated_by=query.from_user.id)
        await _send_done(client, chat_id, t(_LANG, "status.setting_updated"), TOKEN_DEV_SETTINGS)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_INSTALL_LIMITS']}$") & _pm_dev)
    @developer_only
    async def dev_install_limits(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        resp, value = await _ask_value(
            client,
            chat_id,
            "ask.limit_value",
            lambda message: parse_bounded_int(
                getattr(message, "text", None),
                min_value=0,
                max_value=MAX_LIMIT_VALUE,
            ),
            t(_LANG, "common.errors.invalid_non_negative_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_SETTINGS,
        )
        if resp.message is None:
            return

        await settings_repo.set_bot_setting("max_group_members", str(value), updated_by=query.from_user.id)
        await _send_done(client, chat_id, t(_LANG, "status.limit_updated", value=value), TOKEN_DEV_SETTINGS)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_SET_LOG_CHANNEL']}$") & _pm_dev)
    @developer_only
    async def dev_set_log_channel(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        resp, target = await _ask_value(
            client,
            chat_id,
            "ask.chat_id",
            lambda message: (
                parsed
                if (parsed := parse_user_id(getattr(message, "text", "") or ""))
                not in (None, 0)
                else None
            ),
            t(_LANG, "common.errors.invalid_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_SETTINGS,
        )
        if await notify_ask_abort(client, chat_id, resp, return_to=TOKEN_DEV_SETTINGS, lang=_LANG):
            return

        await settings_repo.set_bot_setting("log_channel_id", str(target), updated_by=query.from_user.id)
        await _send_done(client, chat_id, t(_LANG, "log_channel.set", channel=str(target)), TOKEN_DEV_SETTINGS)

    # ── List groups ───────────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_LIST_GROUPS']}$") & _pm_dev)
    @developer_only
    async def dev_list_groups(client: Client, query: CallbackQuery):
        await query.answer()
        await _render_dev_report_page(query, _SOURCE_GROUPS, 0)

    @bot.on_callback_query(filters.regex(r"^pg:dg:\d+$") & _pm_dev)
    @developer_only
    async def dev_list_groups_page(client: Client, query: CallbackQuery):
        await query.answer()
        page = int(query.data.split(":")[2])
        await _render_dev_report_page(query, _SOURCE_GROUPS, page)

    # ── List channels ─────────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_LIST_CHANNELS']}$") & _pm_dev)
    @developer_only
    async def dev_list_channels(client: Client, query: CallbackQuery):
        await query.answer()
        await _render_dev_report_page(query, _SOURCE_CHANNELS, 0)

    @bot.on_callback_query(filters.regex(r"^pg:dc:\d+$") & _pm_dev)
    @developer_only
    async def dev_list_channels_page(client: Client, query: CallbackQuery):
        await query.answer()
        page = int(query.data.split(":")[2])
        await _render_dev_report_page(query, _SOURCE_CHANNELS, page)

    # ── List no-credit ────────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_LIST_NO_CREDIT']}$") & _pm_dev)
    @developer_only
    async def dev_list_no_credit(client: Client, query: CallbackQuery):
        await query.answer()
        await _render_dev_report_page(query, _SOURCE_NO_CREDIT, 0)

    @bot.on_callback_query(filters.regex(r"^pg:dnc:\d+$") & _pm_dev)
    @developer_only
    async def dev_list_no_credit_page(client: Client, query: CallbackQuery):
        await query.answer()
        page = int(query.data.split(":")[2])
        await _render_dev_report_page(query, _SOURCE_NO_CREDIT, page)


    # ── List renewal groups ───────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_LIST_RENEWAL_GROUPS']}$") & _pm_dev)
    @developer_only
    async def dev_list_renewal_groups(client: Client, query: CallbackQuery):
        await query.answer()
        await _render_dev_report_page(query, _SOURCE_RENEWAL, 0)

    @bot.on_callback_query(filters.regex(r"^pg:drn:\d+$") & _pm_dev)
    @developer_only
    async def dev_list_renewal_groups_page(client: Client, query: CallbackQuery):
        await query.answer()
        page = int(query.data.split(":")[2])
        await _render_dev_report_page(query, _SOURCE_RENEWAL, page)

    # ── Extended resource lists ───────────────────────────────────────────
    extended_list_callbacks = {
        CB["DEV_LIST_UNLIMITED_GROUPS"]: _SOURCE_UNLIMITED,
        CB["DEV_LIST_CALL_SECURITY_GROUPS"]: _SOURCE_CALL_SECURITY,
        CB["DEV_LIST_PLAYBACK_GROUPS"]: _SOURCE_PLAYBACK,
        CB["DEV_LIST_TEST_GROUPS"]: _SOURCE_TRIAL,
        CB["DEV_LIST_MUSIC_GROUPS"]: _SOURCE_MUSIC,
        CB["DEV_LIST_VIDEO_GROUPS"]: _SOURCE_VIDEO,
        CB["DEV_LIST_INACTIVE_GROUPS"]: _SOURCE_INACTIVE_GROUPS,
        CB["DEV_LIST_INACTIVE_CHANNELS"]: _SOURCE_INACTIVE_CHANNELS,
    }

    @bot.on_callback_query(
        filters.regex(
            r"^dev:list:(unlimited_groups|call_security_groups|playback_groups|"
            r"test_groups|music_groups|video_groups|inactive_groups|inactive_channels)$"
        )
        & _pm_dev
    )
    @developer_only
    async def dev_list_extended(client: Client, query: CallbackQuery):
        source = extended_list_callbacks.get(query.data or "")
        if source is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer()
        await _render_dev_report_page(query, source, 0)

    @bot.on_callback_query(filters.regex(r"^pg:dx:[a-z]:\d+$") & _pm_dev)
    @developer_only
    async def dev_list_extended_page(client: Client, query: CallbackQuery):
        parts = (query.data or "").split(":")
        source = parts[2]
        if source not in _EXTENDED_LIST_SOURCES:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer()
        await _render_dev_report_page(query, source, max(0, int(parts[3])))

    # ── User resource lists ───────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_LIST_ACTIVE_USERS']}$") & _pm_dev)
    @developer_only
    async def dev_list_active_users(client: Client, query: CallbackQuery):
        await query.answer()
        await _render_dev_users_page(query, banned=False, page=0)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_LIST_INACTIVE_USERS']}$") & _pm_dev)
    @developer_only
    async def dev_list_inactive_users(client: Client, query: CallbackQuery):
        await query.answer()
        await _render_dev_users_page(query, banned=True, page=0)

    @bot.on_callback_query(filters.regex(r"^pg:dux:[ab]:\d+$") & _pm_dev)
    @developer_only
    async def dev_list_users_page(client: Client, query: CallbackQuery):
        await query.answer()
        parts = (query.data or "").split(":")
        banned = parts[2] == _USERS_BANNED
        await _render_dev_users_page(query, banned=banned, page=max(0, int(parts[3])))

    # ── List row actions (detail / credit / back) ─────────────────────────
    @bot.on_callback_query(filters.regex(r"^dev:list:detail:[gcnrusptavix]:[gc]:-?\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_list_detail(client: Client, query: CallbackQuery):
        await query.answer()
        parsed = _parse_list_row_payload(query.data, CB["DEV_LIST_DETAIL_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_number"), show_alert=True)
            return
        source, chat_type, chat_id, page = parsed
        if source == _SOURCE_MANUAL:
            return
        await _render_dev_list_detail(query, source, chat_type, chat_id, page)

    @bot.on_callback_query(filters.regex(r"^dev:list:back:[gcnrusptavix]:\d+$") & _pm_dev)
    @developer_only
    async def dev_list_back(client: Client, query: CallbackQuery):
        await query.answer()
        raw = query.data[len(CB["DEV_LIST_BACK_PREFIX"]):]
        parts = raw.split(":")
        if len(parts) != 2 or not _valid_list_source(parts[0]):
            await query.answer(t(_LANG, "common.errors.invalid_number"), show_alert=True)
            return
        try:
            page = max(0, int(parts[1]))
        except ValueError:
            await query.answer(t(_LANG, "common.errors.invalid_number"), show_alert=True)
            return
        await _render_dev_report_page(query, parts[0], page)

    @bot.on_callback_query(filters.regex(r"^dev:list:credit:inc:[gcnrusptavix]:[gc]:-?\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_list_credit_inc(client: Client, query: CallbackQuery):
        await query.answer()
        parsed = _parse_list_row_payload(query.data, CB["DEV_LIST_CREDIT_INC_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_number"), show_alert=True)
            return
        source, chat_type, chat_id, page = parsed
        if source == _SOURCE_MANUAL:
            return
        await _apply_list_row_credit(
            client,
            query,
            source=source,
            chat_type=chat_type,
            chat_id=chat_id,
            page=page,
            increase=True,
        )

    @bot.on_callback_query(filters.regex(r"^dev:list:credit:dec:[gcnrusptavix]:[gc]:-?\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_list_credit_dec(client: Client, query: CallbackQuery):
        await query.answer()
        parsed = _parse_list_row_payload(query.data, CB["DEV_LIST_CREDIT_DEC_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_number"), show_alert=True)
            return
        source, chat_type, chat_id, page = parsed
        if source == _SOURCE_MANUAL:
            return
        await _apply_list_row_credit(
            client,
            query,
            source=source,
            chat_type=chat_type,
            chat_id=chat_id,
            page=page,
            increase=False,
        )

    # ── Filters management ────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_FILTERS']}$") & _pm_dev)
    @developer_only
    async def dev_filters(client: Client, query: CallbackQuery):
        await query.answer()
        words = await filter_repo.get_filter_words()
        chat_id = query.message.chat.id
        if words:
            text = t(_LANG, "filter_mgmt.list_title") + "\n" + "\n".join(
                t(_LANG, "list_fmt.word_item", word=w) for w in words
            )
        else:
            text = t(_LANG, "filter_mgmt.list_empty")
        await query.message.edit_text(text, reply_markup=build_done_kb(_LANG, TOKEN_DEV_MODERATION))

        resp, word = await _ask_value(
            client,
            chat_id,
            "ask.word_input",
            lambda message: (
                value
                if (value := (getattr(message, "text", "") or "").strip())
                and value != "-"
                else None
            ),
            t(_LANG, "texts_links.invalid_text"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_MODERATION,
        )
        if await notify_ask_abort(client, chat_id, resp, return_to=TOKEN_DEV_MODERATION, lang=_LANG):
            return

        if word.startswith("-"):
            removed = word[1:].strip()
            await filter_repo.remove_filter_word(removed)
            await invalidate_filterwords()
            await _send_done(
                client,
                chat_id,
                t(_LANG, "filter_mgmt.removed", word=removed),
                TOKEN_DEV_MODERATION,
            )
            return

        await filter_repo.add_filter_word(word, added_by=query.from_user.id)
        await invalidate_filterwords()
        await _send_done(
            client,
            chat_id,
            t(_LANG, "filter_mgmt.added", word=word),
            TOKEN_DEV_MODERATION,
        )
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_BLACKLIST']}$") & _pm_dev)
    @developer_only
    async def dev_blacklist(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id

        entries = await blacklist_repo.get_blacklist()
        await query.message.edit_text(
            _build_blacklist_text(entries),
            reply_markup=build_done_kb(_LANG, TOKEN_DEV_MODERATION),
        )

        def _parse_blacklist_input(message):
            raw = (getattr(message, "text", "") or "").strip()
            removing = raw.startswith("-")
            entity_id = parse_user_id(raw[1:] if removing else raw)
            return (removing, entity_id) if entity_id is not None else None

        resp, parsed_input = await _ask_value(
            client,
            chat_id,
            "ask.user_id",
            _parse_blacklist_input,
            t(_LANG, "common.errors.invalid_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_MODERATION,
        )
        if await notify_ask_abort(client, chat_id, resp, return_to=TOKEN_DEV_MODERATION, lang=_LANG):
            return

        removing, entity_id = parsed_input
        if removing:
            entity_type = "group" if entity_id < 0 else "user"
            await blacklist_repo.remove_from_blacklist(entity_id, entity_type)
            await _send_done(
                client,
                chat_id,
                t(_LANG, "blacklist_mgmt.unblocked", entity=str(entity_id)),
                TOKEN_DEV_MODERATION,
            )
            return

        entity_type = "group" if entity_id < 0 else "user"
        await blacklist_repo.add_to_blacklist(
            entity_id,
            entity_type,
            blocked_by=query.from_user.id,
        )
        await _send_done(
            client,
            chat_id,
            t(_LANG, "blacklist_mgmt.blocked", entity=str(entity_id)),
            TOKEN_DEV_MODERATION,
        )


    # ── Force join management ─────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_FORCE_JOIN_MANAGE']}$") & _pm_dev)
    @developer_only
    async def dev_force_join_manage(client: Client, query: CallbackQuery):
        await query.answer()
        await _render_force_join_manage_home(query)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_FORCE_JOIN_ADD']}$") & _pm_dev)
    @developer_only
    async def dev_force_join_add(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id

        def _parse_force_join_identifier(message) -> str | None:
            raw = (getattr(message, "text", "") or "").strip()
            if raw.startswith("@") and len(raw) > 1:
                return raw
            parsed = parse_user_id(raw)
            return str(parsed) if parsed is not None else None

        resp, identifier = await _ask_value(
            client,
            chat_id,
            "force_join_mgmt.add_prompt",
            _parse_force_join_identifier,
            t(_LANG, "force_join_mgmt.invalid_identifier"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_FORCE_JOIN,
        )
        if await notify_ask_abort(client, chat_id, resp, return_to=TOKEN_DEV_FORCE_JOIN, lang=_LANG):
            return

        result = await ForcedMembershipService.add_target(client, identifier, query.from_user.id)
        if "error" in result:
            await _render_force_join_manage_home(
                query,
                notice=t(_LANG, "force_join_mgmt.add_inaccessible"),
            )
            return

        await AdminDashboardService.invalidate_cache("general")
        notice = t(_LANG, "force_join_mgmt.added", channel=str(result["target"].channel_id))
        if result.get("verify_status") == "bot_not_admin":
            notice += "\n" + t(_LANG, "force_join_mgmt.add_bot_not_admin")
        await _render_force_join_list(query, 0, notice=notice)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_FORCE_JOIN_LIST']}$") & _pm_dev)
    @developer_only
    async def dev_force_join_list(client: Client, query: CallbackQuery):
        await query.answer()
        await _render_force_join_list(query, 0)

    @bot.on_callback_query(filters.regex(r"^dev:fj:pg:\d+$") & _pm_dev)
    @developer_only
    async def dev_force_join_list_page(client: Client, query: CallbackQuery):
        await query.answer()
        page = int(query.data.split(":")[3])
        await _render_force_join_list(query, page)

    @bot.on_callback_query(filters.regex(r"^dev:fj:rm:-?\d+:\d+$") & _pm_dev)
    @developer_only
    async def dev_force_join_remove(client: Client, query: CallbackQuery):
        await query.answer()
        parsed = _parse_force_join_remove_request(query.data)
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        channel_id, page = parsed

        target = await force_join_repo.get_active_target_by_channel_id(channel_id)
        if target is None:
            await _render_force_join_list(
                query,
                page,
                notice=t(_LANG, "force_join_mgmt.remove_not_found", channel=str(channel_id)),
            )
            return

        issued_at = int(time.time())
        await query.message.edit_text(
            t(
                _LANG,
                "force_join_mgmt.remove_confirm_prompt",
                channel_id=channel_id,
            ),
            reply_markup=_fj_remove_confirm_kb(
                channel_id,
                page,
                query.from_user.id,
                issued_at,
            ),
        )

    @bot.on_callback_query(
        filters.regex(r"^dev:fj:rm:do:-?\d+:\d+:\d+:\d+$") & _pm_dev
    )
    @developer_only
    async def dev_force_join_remove_confirm(client: Client, query: CallbackQuery):
        parsed = _parse_fj_remove_bound(query.data, CB["DEV_FORCE_JOIN_REMOVE_EXEC_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        channel_id, page, bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_fj_remove_token(issued_at):
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return

        target = await force_join_repo.get_active_target_by_channel_id(channel_id)
        if target is None:
            await query.answer()
            await _render_force_join_list(
                query,
                page,
                notice=t(_LANG, "force_join_mgmt.remove_not_found", channel=str(channel_id)),
            )
            return

        await query.answer()
        await ForcedMembershipService.remove_target(channel_id)
        await AdminDashboardService.invalidate_cache("general")
        await _render_force_join_list(
            query,
            page,
            notice=t(_LANG, "force_join_mgmt.removed", channel=str(channel_id)),
        )

    @bot.on_callback_query(
        filters.regex(r"^dev:fj:rm:no:-?\d+:\d+:\d+:\d+$") & _pm_dev
    )
    @developer_only
    async def dev_force_join_remove_abort(client: Client, query: CallbackQuery):
        parsed = _parse_fj_remove_bound(query.data, CB["DEV_FORCE_JOIN_REMOVE_ABORT_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        channel_id, page, bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_fj_remove_token(issued_at):
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        await _render_force_join_list(query, page)

    @bot.on_callback_query(
        filters.regex(r"^dev:fj:rm:(?:do|no):.*") & _pm_dev
    )
    @developer_only
    async def dev_force_join_remove_malformed(client: Client, query: CallbackQuery):  # noqa: ARG001
        if _parse_fj_remove_bound(query.data, CB["DEV_FORCE_JOIN_REMOVE_EXEC_PREFIX"]) is not None:
            return
        if _parse_fj_remove_bound(query.data, CB["DEV_FORCE_JOIN_REMOVE_ABORT_PREFIX"]) is not None:
            return
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)

    # ── Sudo management ───────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_SUDO_MANAGE']}$") & _pm_dev)
    @developer_only
    async def dev_sudo_manage(client: Client, query: CallbackQuery):
        await query.answer()
        sudos = await user_repo.get_all_sudos()
        chat_id = query.message.chat.id
        if sudos:
            text = t(_LANG, "sudo_mgmt.list_title") + "\n"
            text += "\n".join(
                t(_LANG, "list_fmt.user_item", user_id=s.user_id, username=s.username or t(_LANG, "reports.value_unavailable"))
                for s in sudos
            )
        else:
            text = t(_LANG, "sudo_mgmt.list_empty")
        await query.message.edit_text(text, reply_markup=build_done_kb(_LANG, TOKEN_DEV_USERS))

        def _parse_sudo_input(message):
            raw = (getattr(message, "text", "") or "").strip()
            removing = raw.startswith("-")
            uid = parse_user_id(raw[1:] if removing else raw)
            return (removing, uid) if uid is not None and uid > 0 else None

        resp, parsed_input = await _ask_value(
            client,
            chat_id,
            "ask.user_id",
            _parse_sudo_input,
            t(_LANG, "common.errors.invalid_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_USERS,
        )
        if await notify_ask_abort(client, chat_id, resp, return_to=TOKEN_DEV_USERS, lang=_LANG):
            return

        removing, uid = parsed_input
        if removing:
            sudo = await user_repo.get_sudo(uid)
            if sudo is None:
                await _send_done(client, chat_id, t(_LANG, "sudo_mgmt.not_found"), TOKEN_DEV_USERS)
                return
            issued_at = int(time.time())
            await deliver_ask_outcome(
                client,
                chat_id,
                query.from_user.id,
                t(_LANG, "sudo_mgmt.remove_confirm_prompt", user=str(uid)),
                KeyboardFactory.dev_privileged_user_remove_confirm(
                    _LANG, "sudo", uid, query.from_user.id, issued_at
                ),
            )
            return

        await user_repo.add_sudo(uid, added_by=query.from_user.id)
        await invalidate_sudolist()
        try:
            sudo_row = await user_repo.get_sudo(uid)
            username = getattr(sudo_row, "username", None) if sudo_row else None
            await NotificationService.notify_sudo_added(
                client,
                uid,
                query.from_user.id,
                username=username,
            )
        except Exception:
            logger.debug("notify_sudo_added failed", exc_info=True)
        await _send_done(client, chat_id, t(_LANG, "sudo_mgmt.added", user=str(uid)), TOKEN_DEV_USERS)
        from app.utils.safe_sender import safe_send_message

        await safe_send_message(client, uid, t(_LANG, "sudo_mgmt.welcome_dm"))

    # ── Set owner ─────────────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_SET_OWNER']}$") & _pm_dev)
    @developer_only
    async def dev_set_owner(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        resp, uid = await _ask_value(
            client,
            chat_id,
            "ask.user_id",
            lambda message: (
                parsed
                if (parsed := parse_user_id(getattr(message, "text", "") or ""))
                is not None
                and parsed > 0
                else None
            ),
            t(_LANG, "common.errors.invalid_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_USERS,
        )
        if await notify_ask_abort(client, chat_id, resp, return_to=TOKEN_DEV_USERS, lang=_LANG):
            return

        await user_repo.add_owner(uid, added_by=query.from_user.id)
        await settings_repo.set_bot_setting(
            "start_customization_owner_user_id",
            str(uid),
            updated_by=query.from_user.id,
        )
        await invalidate_ownerlist()
        try:
            owner_row = await user_repo.get_owner(uid)
            username = getattr(owner_row, "username", None) if owner_row else None
            await NotificationService.notify_owner_added(
                client,
                uid,
                query.from_user.id,
                username=username,
            )
        except Exception:
            logger.debug("notify_owner_added failed", exc_info=True)
        await _send_done(client, chat_id, t(_LANG, "owner_mgmt.set", user=str(uid)), TOKEN_DEV_USERS)
    # ── Leave group/chat ─────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_LEAVE_GROUP']}$") & _pm_dev)
    @developer_only
    async def dev_leave_group(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        resp, target_id = await _ask_value(
            client,
            chat_id,
            "ask.chat_id",
            lambda message: parse_user_id(getattr(message, "text", "") or ""),
            t(_LANG, "common.errors.invalid_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_LISTS,
        )
        if await notify_ask_abort(client, chat_id, resp, return_to=TOKEN_DEV_LISTS, lang=_LANG):
            return

        row = await admin_report_repo.get_install_row(target_id)
        if row is None:
            await query.message.edit_text(
                t(_LANG, "reports.leave_not_found", chat_id=target_id),
                reply_markup=build_done_kb(_LANG, TOKEN_DEV_LISTS),
            )
            return

        await query.message.edit_text(
            t(
                _LANG,
                "reports.leave_confirm_prompt",
                chat_id=target_id,
                title=row.title or str(target_id),
            ),
            reply_markup=_leave_confirm_kb(target_id, _SOURCE_MANUAL, 0),
        )

    @bot.on_callback_query(filters.regex(r"^dev:leave:cfm:-?\d+:[gcnrmusptavix]:\d+$") & _pm_dev)
    @developer_only
    async def dev_leave_confirm(client: Client, query: CallbackQuery):
        await query.answer()
        parsed = _parse_leave_payload(query.data, CB["DEV_LEAVE_CONFIRM_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_number"), show_alert=True)
            return

        chat_id, source, page = parsed
        row = await admin_report_repo.get_install_row(chat_id)
        if row is None:
            if source == _SOURCE_MANUAL:
                await query.message.edit_text(
                    t(_LANG, "reports.leave_not_found", chat_id=chat_id),
                    reply_markup=build_done_kb(_LANG, TOKEN_DEV_LISTS),
                )
            else:
                await _render_dev_report_page(
                    query,
                    source,
                    page,
                    notice=t(_LANG, "reports.leave_not_found", chat_id=chat_id),
                )
            return

        await query.message.edit_text(
            t(
                _LANG,
                "reports.leave_confirm_prompt",
                chat_id=chat_id,
                title=row.title or str(chat_id),
            ),
            reply_markup=_leave_confirm_kb(chat_id, source, page),
        )

    @bot.on_callback_query(filters.regex(r"^dev:leave:no:-?\d+:[gcnrmusptavix]:\d+$") & _pm_dev)
    @developer_only
    async def dev_leave_cancel(client: Client, query: CallbackQuery):
        await query.answer()
        parsed = _parse_leave_payload(query.data, CB["DEV_LEAVE_CANCEL_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_number"), show_alert=True)
            return

        _chat_id, source, page = parsed
        if source == _SOURCE_MANUAL:
            await query.message.edit_text(
                t(_LANG, "common.cancelled"),
                reply_markup=build_done_kb(_LANG, TOKEN_DEV_LISTS),
            )
            return

        await _render_dev_report_page(
            query,
            source,
            page,
            notice=t(_LANG, "reports.leave_cancelled"),
        )

    @bot.on_callback_query(filters.regex(r"^dev:leave:do:-?\d+:[gcnrmusptavix]:\d+$") & _pm_dev)
    @developer_only
    async def dev_leave_execute(client: Client, query: CallbackQuery):
        await query.answer()
        parsed = _parse_leave_payload(query.data, CB["DEV_LEAVE_EXEC_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_number"), show_alert=True)
            return

        chat_id, source, page = parsed
        row = await _leave_install(client, chat_id, query.from_user.id)
        if row is None:
            if source == _SOURCE_MANUAL:
                await query.message.edit_text(
                    t(_LANG, "reports.leave_not_found", chat_id=chat_id),
                    reply_markup=build_done_kb(_LANG, TOKEN_DEV_LISTS),
                )
            else:
                await _render_dev_report_page(
                    query,
                    source,
                    page,
                    notice=t(_LANG, "reports.leave_not_found", chat_id=chat_id),
                )
            return

        success = t(
            _LANG,
            "reports.leave_done",
            chat_id=chat_id,
            title=row.title or str(chat_id),
        )
        if source == _SOURCE_MANUAL:
            await query.message.edit_text(
                success,
                reply_markup=build_done_kb(_LANG, TOKEN_DEV_LISTS),
            )
            return

        await _render_dev_report_page(query, source, page, notice=success)
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_INSTALL_POLICY']}$") & _pm_dev)
    @developer_only
    async def dev_install_policy(client: Client, query: CallbackQuery):
        await render_install_policy_panel(client, query)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_INSTALL_POLICY_MODE']}$") & _pm_dev)
    @developer_only
    async def dev_install_policy_mode(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id

        def _parse_mode(message) -> str | None:
            value = (getattr(message, "text", "") or "").strip().lower()
            return value if value in {"paid", "free", "hybrid", "open"} else None

        resp, mode = await _ask_value(
            client,
            chat_id,
            "ask.text_input",
            _parse_mode,
            t(_LANG, "admin.install_policy.invalid_mode"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_INSTALL_POLICY,
        )
        if await notify_ask_abort(client, chat_id, resp, return_to=TOKEN_DEV_INSTALL_POLICY, lang=_LANG):
            return
        policy = await InstallPolicyService.get_policy()
        trial_days = policy.trial_days if policy else 3
        group_fee = policy.group_install_fee_irr if policy else 0
        chan_fee = policy.chan_install_fee_irr if policy else 0
        await InstallPolicyService.update_policy(mode, trial_days, group_fee, chan_fee)
        await _deliver_install_policy_outcome(
            client,
            query,
            t(
                _LANG,
                "admin.install_policy.mode_updated",
                mode=label(_LANG, "install_policy_mode", mode),
            ),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_INSTALL_POLICY_TRIAL']}$") & _pm_dev)
    @developer_only
    async def dev_install_policy_trial(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        resp, days = await _ask_value(
            client,
            chat_id,
            "ask.amount_days",
            lambda message: parse_bounded_int(
                getattr(message, "text", None),
                min_value=1,
                max_value=MAX_CREDIT_DAYS,
            ),
            t(_LANG, "common.errors.invalid_positive_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_INSTALL_POLICY,
        )
        if await notify_ask_abort(client, chat_id, resp, return_to=TOKEN_DEV_INSTALL_POLICY, lang=_LANG):
            return
        policy = await InstallPolicyService.get_policy()
        mode = policy.policy_mode if policy else "open"
        group_fee = policy.group_install_fee_irr if policy else 0
        chan_fee = policy.chan_install_fee_irr if policy else 0
        await InstallPolicyService.update_policy(mode, days, group_fee, chan_fee)
        await _deliver_install_policy_outcome(
            client,
            query,
            t(_LANG, "admin.install_policy.trial_updated", days=days),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_INSTALL_POLICY_FEE_GROUP']}$") & _pm_dev)
    @developer_only
    async def dev_install_policy_fee_group(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        resp, fee = await _ask_value(
            client,
            chat_id,
            "ask.rate_value",
            lambda message: parse_bounded_int(
                getattr(message, "text", None),
                min_value=0,
                max_value=MAX_RATE_VALUE,
            ),
            t(_LANG, "common.errors.invalid_non_negative_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_INSTALL_POLICY,
        )
        if await notify_ask_abort(client, chat_id, resp, return_to=TOKEN_DEV_INSTALL_POLICY, lang=_LANG):
            return
        policy = await InstallPolicyService.get_policy()
        mode = policy.policy_mode if policy else "open"
        trial_days = policy.trial_days if policy else 3
        chan_fee = policy.chan_install_fee_irr if policy else 0
        await InstallPolicyService.update_policy(mode, trial_days, fee, chan_fee)
        await _deliver_install_policy_outcome(
            client,
            query,
            t(_LANG, "admin.install_policy.fee_updated"),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_INSTALL_POLICY_FEE_CHAN']}$") & _pm_dev)
    @developer_only
    async def dev_install_policy_fee_chan(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        resp, fee = await _ask_value(
            client,
            chat_id,
            "ask.rate_value",
            lambda message: parse_bounded_int(
                getattr(message, "text", None),
                min_value=0,
                max_value=MAX_RATE_VALUE,
            ),
            t(_LANG, "common.errors.invalid_non_negative_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_INSTALL_POLICY,
        )
        if await notify_ask_abort(client, chat_id, resp, return_to=TOKEN_DEV_INSTALL_POLICY, lang=_LANG):
            return
        policy = await InstallPolicyService.get_policy()
        mode = policy.policy_mode if policy else "open"
        trial_days = policy.trial_days if policy else 3
        group_fee = policy.group_install_fee_irr if policy else 0
        await InstallPolicyService.update_policy(mode, trial_days, group_fee, fee)
        await _deliver_install_policy_outcome(
            client,
            query,
            t(_LANG, "admin.install_policy.fee_updated"),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_INSTALL_POLICY_WHITELIST']}$") & _pm_dev)
    @developer_only
    async def dev_install_policy_whitelist(client: Client, query: CallbackQuery):
        await query.answer()
        text = t(_LANG, "admin.install_policy.whitelist_title")
        await query.message.edit_text(
            text,
            reply_markup=KeyboardFactory.install_policy_whitelist_panel(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_INSTALL_POLICY_WHITELIST_ADD']}$") & _pm_dev)
    @developer_only
    async def dev_install_policy_whitelist_add(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        resp, target_id = await _ask_value(
            client,
            chat_id,
            "ask.chat_id",
            lambda message: parse_user_id(getattr(message, "text", None)),
            t(_LANG, "common.errors.invalid_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_INSTALL_POLICY,
        )
        if await notify_ask_abort(client, chat_id, resp, return_to=TOKEN_DEV_INSTALL_POLICY, lang=_LANG):
            return
        await InstallPolicyService.add_whitelist(
            target_id, created_by=query.from_user.id
        )
        await _deliver_install_policy_outcome(
            client,
            query,
            t(_LANG, "admin.install_policy.whitelist_added", chat_id=target_id),
            whitelist=True,
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_INSTALL_POLICY_WHITELIST_RM']}$") & _pm_dev)
    @developer_only
    async def dev_install_policy_whitelist_rm(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        resp, target_id = await _ask_value(
            client,
            chat_id,
            "ask.chat_id",
            lambda message: parse_user_id(getattr(message, "text", None)),
            t(_LANG, "common.errors.invalid_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_INSTALL_POLICY,
        )
        if await notify_ask_abort(client, chat_id, resp, return_to=TOKEN_DEV_INSTALL_POLICY, lang=_LANG):
            return
        await InstallPolicyService.remove_whitelist(target_id)
        await _deliver_install_policy_outcome(
            client,
            query,
            t(_LANG, "admin.install_policy.whitelist_removed", chat_id=target_id),
            whitelist=True,
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_INSTALL_POLICY_WHITELIST_LIST']}$") & _pm_dev)
    @developer_only
    async def dev_install_policy_whitelist_list(client: Client, query: CallbackQuery):
        entries = await InstallPolicyService.get_whitelist()
        if not entries:
            await query.answer(t(_LANG, "filter_mgmt.list_empty"), show_alert=True)
            return
        lines = [t(_LANG, "list_fmt.chat_item", chat_id=e.chat_id, chat_type=e.chat_type) for e in entries[:50]]
        await query.message.edit_text(
            "\n".join(lines),
            reply_markup=KeyboardFactory.install_policy_whitelist_panel(_LANG),
        )

    # ── Sudo link management ──────────────────────────────────────────

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_SUDO_LINK_MENU']}$") & _pm_dev)
    @developer_only
    async def dev_sudo_link_menu(client: Client, query: CallbackQuery):
        await query.answer(t(_LANG, "sudo_mgmt.link_removed_notice"), show_alert=True)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_SUDO_LINK_SET']}$") & _pm_dev)
    @developer_only
    async def dev_sudo_link_set(client: Client, query: CallbackQuery):
        await query.answer(t(_LANG, "sudo_mgmt.link_removed_notice"), show_alert=True)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_SUDO_LINK_RM']}$") & _pm_dev)
    @developer_only
    async def dev_sudo_link_rm(client: Client, query: CallbackQuery):
        await query.answer(t(_LANG, "sudo_mgmt.link_removed_notice"), show_alert=True)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_SUDO_LINK_LIST']}$") & _pm_dev)
    @developer_only
    async def dev_sudo_link_list(client: Client, query: CallbackQuery):
        await query.answer(t(_LANG, "sudo_mgmt.link_removed_notice"), show_alert=True)








