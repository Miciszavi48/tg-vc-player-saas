"""Shared sudo permission detail UI helpers for developer and owner panels."""

from __future__ import annotations

import time

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.repositories import user_repo
from app.repositories.user_repo import (
    SUDO_PERMISSION_FIELD_LABEL_KEYS,
    SUDO_PERMISSION_FIELD_NAMES,
    SUDO_PERMISSION_FIELD_TO_CALLBACK_KEY,
    resolve_sudo_permission_field,
)
from app.utils.button_style import mark_toggle_state
from app.utils.helpers import parse_user_id
from app.utils.i18n import t
from app.utils.ui import CB, compatible_inline_button


def permission_state_label(lang: str, enabled: bool) -> str:
    """Return localized enabled/disabled label for a permission flag."""
    key = "sudo_permissions.state_enabled" if enabled else "sudo_permissions.state_disabled"
    return t(lang, key)


def permission_field_label(lang: str, field: str) -> str:
    """Return localized label for a sudo permission field."""
    label_key = SUDO_PERMISSION_FIELD_LABEL_KEYS.get(field, "sudo_permissions.perm_groups")
    return t(lang, label_key)


def permission_callback_key(field: str) -> str | None:
    """Return compact callback key for a permission field."""
    return SUDO_PERMISSION_FIELD_TO_CALLBACK_KEY.get(field)


def parse_sudo_detail_payload(data: str, prefix: str) -> tuple[int, int] | None:
    """Parse ``{prefix}{user_id}:{page}`` sudo detail callback payloads."""
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix) :].split(":")
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


def parse_sudo_perm_toggle_payload(data: str, prefix: str) -> tuple[str, int, int] | None:
    """Parse ``{prefix}{key}:{user_id}:{page}`` permission toggle payloads."""
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix) :].split(":")
    if len(parts) != 3:
        return None
    key, target_raw, page_raw = parts
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


def parse_sudo_perm_confirm_bound(data: str, prefix: str) -> tuple[str, int, int, int, int] | None:
    """Parse bound sudo permission confirm/abort payloads."""
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix) :].split(":")
    if len(parts) != 5:
        return None
    key, target_raw, page_raw, actor_raw, issued_raw = parts
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


def build_owner_sudo_perm_confirm_kb(
    lang: str,
    key: str,
    target_uid: int,
    page: int,
    actor_id: int,
    issued_at: int,
) -> InlineKeyboardMarkup:
    """Build owner-scoped disable-confirm keyboard for a sudo permission."""
    payload = f"{key}:{target_uid}:{page}:{actor_id}:{issued_at}"
    return InlineKeyboardMarkup(
        [
            [
                compatible_inline_button(
                    t(lang, "common.buttons.confirm"),
                    callback_data=f"{CB['OWN_SUDO_PERM_DO_PREFIX']}{payload}",
                ),
                compatible_inline_button(
                    t(lang, "common.buttons.cancel_inline"),
                    callback_data=f"{CB['OWN_SUDO_PERM_NO_PREFIX']}{payload}",
                ),
            ],
            [
                InlineKeyboardButton(
                    t(lang, "sudo_permissions.back_to_detail"),
                    callback_data=f"{CB['OWN_SUDO_DETAIL_PREFIX']}{target_uid}:{page}",
                ),
            ],
        ]
    )


def build_owner_sudo_detail_kb(sudo, page: int, lang: str) -> InlineKeyboardMarkup:
    """Build owner-scoped sudo detail keyboard with enforced permission toggles only."""
    perms = user_repo.sudo_permissions_from_row(sudo)
    rows: list[list[InlineKeyboardButton]] = []
    for field in SUDO_PERMISSION_FIELD_NAMES:
        key = permission_callback_key(field)
        if key is None:
            continue
        enabled = perms[field]
        label = permission_field_label(lang, field)
        btn_key = (
            "sudo_permissions.toggle_disable_btn"
            if enabled
            else "sudo_permissions.toggle_enable_btn"
        )
        button = InlineKeyboardButton(
            t(lang, btn_key, label=label),
            callback_data=f"{CB['OWN_SUDO_PERM_TOGGLE_PREFIX']}{key}:{sudo.user_id}:{page}",
        )
        mark_toggle_state(button, enabled)
        rows.append([button])
    rows.append(
        [
            InlineKeyboardButton(
                t(lang, "sudo_permissions.back_to_list"),
                callback_data=f"{CB['OWN_SUDO_LIST_BACK_PREFIX']}{page}",
            ),
        ]
    )
    rows.append([InlineKeyboardButton(t(lang, "common.buttons.home"), callback_data=CB["WZ_HOME"])])
    return InlineKeyboardMarkup(rows)


def build_owner_sudo_list_kb(
    entries: list,
    page: int,
    total_pages: int,
    page_prefix: str,
    return_to: str,
    lang: str,
) -> InlineKeyboardMarkup:
    """Build owner sudo list keyboard with per-row detail buttons."""
    rows: list[list[InlineKeyboardButton]] = []
    for entry in entries:
        user_id = getattr(entry, "user_id", 0)
        rows.append(
            [
                InlineKeyboardButton(
                    t(lang, "reports.detail_btn"),
                    callback_data=f"{CB['OWN_SUDO_DETAIL_PREFIX']}{user_id}:{page}",
                )
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                t(lang, "common.buttons.prev"),
                callback_data=f"{page_prefix}{page - 1}",
            )
        )
    if page + 1 < total_pages:
        nav.append(
            InlineKeyboardButton(
                t(lang, "common.buttons.next"),
                callback_data=f"{page_prefix}{page + 1}",
            )
        )
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                t(lang, "common.buttons.back"),
                callback_data=f"{CB['WZ_BACK_PREFIX']}{return_to}",
            )
        ]
    )
    rows.append([InlineKeyboardButton(t(lang, "common.buttons.home"), callback_data=CB["WZ_HOME"])])
    return InlineKeyboardMarkup(rows)


def format_owner_sudo_detail_text(sudo, lang: str) -> str:
    """Render owner-scoped sudo detail text with enforced permission matrix."""
    perms = user_repo.sudo_permissions_from_row(sudo)
    added_at = (
        sudo.added_at.strftime("%Y-%m-%d %H:%M")
        if getattr(sudo, "added_at", None) is not None
        else t(lang, "reports.value_unavailable")
    )
    active_state = permission_state_label(lang, bool(getattr(sudo, "is_active", True)))
    lines = [
        t(lang, "sudo_permissions.owner_detail_title"),
        t(lang, "sudo_permissions.owner_scope_note"),
        "",
        t(
            lang,
            "sudo_permissions.detail_body",
            user_id=sudo.user_id,
            username=sudo.username or t(lang, "reports.value_unavailable"),
            display_name=sudo.display_name or t(lang, "reports.value_unavailable"),
            added_by=sudo.added_by if sudo.added_by is not None else t(lang, "reports.value_unavailable"),
            added_at=added_at,
            is_active=active_state,
            total_installs=int(getattr(sudo, "total_installs", 0) or 0),
            link_state=t(lang, "sudo_permissions.link_not_set"),
        ),
        "",
        t(lang, "sudo_permissions.matrix_title"),
    ]
    for field in SUDO_PERMISSION_FIELD_NAMES:
        lines.append(
            t(
                lang,
                "sudo_permissions.perm_line",
                label=permission_field_label(lang, field),
                state=permission_state_label(lang, perms[field]),
            )
        )
    lines.append(t(lang, "sudo_permissions.perm_auto_admin_note"))
    return "\n".join(lines)


def issue_confirm_bound(actor_id: int) -> tuple[int, int]:
    """Return actor id and issued-at timestamp for bound confirm callbacks."""
    return actor_id, int(time.time())
