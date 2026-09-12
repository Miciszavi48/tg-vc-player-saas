from __future__ import annotations

from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup

from app.repositories import settings_repo, user_repo
from app.services.bot_settings_service import (
    build_start_links,
    get_start_welcome_text,
)
from app.utils.bot_guards import is_bot_enabled, is_developer, is_sudo_panel_enabled
from app.utils.helpers import mention_user
from app.utils.i18n import t
from app.utils.ui import KeyboardFactory


async def detect_private_role(user_id: int) -> str:
    if is_developer(user_id):
        return "developer"
    if await user_repo.is_owner(user_id):
        return "owner"
    if await user_repo.is_sudo(user_id):
        return "sudo"
    return "regular"


async def _build_status_summary(lang: str) -> str:
    feature_keys = [
        ("bot_enabled", "feature_bot_enabled"),
        ("sudo_panel_enabled", "feature_sudo_panel"),
        ("force_join_enabled", "feature_forced_membership"),
        ("auto_leave_enabled", "feature_auto_leave"),
        ("trial_enabled", "feature_trial"),
    ]
    lines = [t(lang, "status_summary.title")]
    bool_defaults = {
        "bot_enabled": True,
        "sudo_panel_enabled": True,
    }
    for setting_key, i18n_suffix in feature_keys:
        if setting_key in bool_defaults:
            is_on = await settings_repo.get_bot_setting_bool(
                setting_key, default=bool_defaults[setting_key]
            )
        else:
            val = await settings_repo.get_bot_setting(setting_key)
            is_on = val in ("1", "true")
        state = t(lang, "status_indicator.active") if is_on else t(lang, "status_indicator.inactive")
        feature_label = t(lang, f"status_summary.{i18n_suffix}")
        lines.append(t(lang, "status_summary.row", feature=feature_label, state=state))
    return "\n".join(lines)


async def build_private_root_payload(
    client: Client | None,
    user_id: int,
    first_name: str,
    lang: str = "fa",
    *,
    include_welcome: bool,
) -> tuple[str, InlineKeyboardMarkup | None]:
    role = await detect_private_role(user_id)
    mention = mention_user(user_id, first_name or str(user_id))
    owner_user_id = user_id if role == "owner" else None
    welcome = await get_start_welcome_text(
        lang,
        mention=mention,
        owner_user_id=owner_user_id,
        resolve_owner=False,
    )

    if not is_developer(user_id) and not await is_bot_enabled():
        links = await build_start_links(client, owner_user_id=owner_user_id)
        text = t(lang, "status.bot_disabled")
        if include_welcome:
            text = welcome + "\n\n" + text
        return text, KeyboardFactory.start_menu(lang, links)

    if role == "sudo" and not await is_sudo_panel_enabled():
        links = await build_start_links(client, owner_user_id=owner_user_id)
        text = t(lang, "status.sudo_panel_disabled")
        if include_welcome:
            text = welcome + "\n\n" + text
        return text, KeyboardFactory.start_menu(lang, links)

    if role in ("developer", "owner"):
        summary = await _build_status_summary(lang)
        panel_key = "developer" if role == "developer" else "owner"
        panel_title = t(lang, f"panels.{panel_key}.title")
        kb = KeyboardFactory.developer_panel(lang) if role == "developer" else KeyboardFactory.owner_panel(lang)
        text = panel_title + "\n\n" + summary
        if include_welcome:
            text = welcome + "\n\n" + text
        return text, kb

    if role == "sudo":
        panel_title = t(lang, "panels.sudo.title")
        text = panel_title
        if include_welcome:
            text = welcome + "\n\n" + panel_title
        perms = await user_repo.get_sudo_permissions(user_id) or {}
        show_leave = perms.get("can_remove_bot", True)
        return text, KeyboardFactory.sudo_panel(lang, show_leave_installs=show_leave)

    links = await build_start_links(client, owner_user_id=owner_user_id)
    from app.services import start_customization_service as start_custom
    rendered_menu = await start_custom.render_start_menu(
        lang,
        links,
        owner_user_id=owner_user_id,
        chat_id=None,
    )
    if rendered_menu.uses_legacy_fallback:
        return welcome, KeyboardFactory.start_menu(lang, links)
    return welcome, KeyboardFactory.start_custom_menu(rendered_menu)


async def build_private_start_home_payload(
    client: Client | None,
    user_id: int,
    first_name: str,
    lang: str = "fa",
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Build the normal private /start home payload for panel-first redraw.

    Privileged users keep exactly one management-panel entry button on the
    start keyboard. Content is text-oriented so callback edit can reuse the
    existing panel message when possible.
    """
    from app.services import start_customization_runtime as start_runtime

    role = await detect_private_role(user_id)
    management_role = role if role in ("developer", "owner", "sudo") else None

    _item, fallback_text, reply_markup = await start_runtime.build_regular_start_payload(
        client,
        user_id=user_id,
        first_name=first_name,
        lang=lang,
    )
    text = (fallback_text or "").strip()
    if _item is not None:
        item_text = (getattr(_item, "text", None) or getattr(_item, "caption", None) or "").strip()
        if item_text:
            text = item_text
    if not text:
        mention = mention_user(user_id, first_name or str(user_id))
        text = await get_start_welcome_text(lang, mention=mention)

    if management_role is not None:
        reply_markup = KeyboardFactory.with_management_entry(
            lang,
            management_role,
            reply_markup,
        )
    return text, reply_markup
