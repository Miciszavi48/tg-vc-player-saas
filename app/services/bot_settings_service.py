from __future__ import annotations

import json
import logging

from pyrogram import Client

from app.config.settings import settings
from app.repositories import settings_repo, user_repo
from app.services.texts_links_ui import decode_setting_value
from app.utils.i18n import t

logger = logging.getLogger(__name__)

LOG_CHANNEL_SETTING_KEY = "log_channel_id"
_GLOBAL_ONLY_EFFECTIVE_KEYS = frozenset()


def _safe_format(template: str, **kwargs: object) -> str:
    try:
        return template.format(**kwargs)
    except Exception:
        return template


def _normalize(value: str | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


async def get_setting_text(key: str, env_default: str = "") -> str:
    raw = await settings_repo.get_bot_setting(key)
    if raw is None:
        return _normalize(env_default)
    return _normalize(raw)


async def get_effective_setting_value(
    key: str,
    *,
    owner_user_id: int | None = None,
    env_default: str = "",
) -> str:
    """Read effective text/link value: owner override, global, then env default.

    Args:
        key: Whitelisted bot_settings / owner_text_links field key.
        owner_user_id: Optional install-lineage owner scope.
        env_default: Fallback when no stored value exists.

    Returns:
        Normalized effective string for runtime consumers.
    """
    if key in _GLOBAL_ONLY_EFFECTIVE_KEYS:
        owner_user_id = None

    if owner_user_id is None:
        return await get_setting_text(key, env_default)

    try:
        from app.services import owner_text_link_service

        effective = await owner_text_link_service.get_effective_text_link(
            key,
            owner_user_id=owner_user_id,
        )
    except Exception:
        logger.debug(
            "Effective read failed for key=%s owner_user_id=%s; using global fallback",
            key,
            owner_user_id,
            exc_info=True,
        )
        return await get_setting_text(key, env_default)

    if effective.mode == "empty":
        return _normalize(env_default)

    if effective.mode == "media":
        parsed = decode_setting_value(effective.raw)
        caption = str(parsed.get("caption") or "").strip()
        if caption:
            return caption
        return _normalize(env_default)

    return _normalize(effective.raw or env_default)


async def resolve_single_active_owner_user_id() -> int | None:
    """Return the designated operational Owner for start customization."""
    configured = await settings_repo.get_bot_setting(
        "start_customization_owner_user_id"
    )
    try:
        configured_owner_id = int(configured or 0)
    except (TypeError, ValueError):
        configured_owner_id = 0
    if configured_owner_id > 0 and await user_repo.is_owner(configured_owner_id):
        return configured_owner_id

    owners = await user_repo.get_all_owners()
    if len(owners) == 1:
        return int(owners[0].user_id)

    from app.utils.bot_guards import is_developer

    operational_owners = [
        owner for owner in owners if not is_developer(int(owner.user_id))
    ]
    if len(operational_owners) == 1:
        return int(operational_owners[0].user_id)

    if owners:
        logger.warning(
            "Start customization owner is ambiguous: active_owner_count=%s "
            "operational_owner_count=%s",
            len(owners),
            len(operational_owners),
        )
    return None


async def get_setting_link(
    key: str,
    env_default: str = "",
    *,
    owner_user_id: int | None = None,
) -> str:
    return await get_effective_setting_value(
        key,
        owner_user_id=owner_user_id,
        env_default=env_default,
    )


async def get_developer_link(*, owner_user_id: int | None = None) -> str:
    pv = await get_setting_link(
        "developer_pv_link",
        "",
        owner_user_id=owner_user_id,
    )
    if pv:
        return pv
    return await get_setting_link(
        "developer_link",
        settings.DEVELOPER_LINK or "",
        owner_user_id=owner_user_id,
    )


async def get_bot_channel_link(*, owner_user_id: int | None = None) -> str:
    return await get_setting_link(
        "bot_channel_link",
        settings.BOT_CHANNEL_LINK or "",
        owner_user_id=owner_user_id,
    )


async def get_guide_channel_link(*, owner_user_id: int | None = None) -> str:
    return await get_setting_link(
        "guide_channel_link",
        settings.GUIDE_CHANNEL_LINK or "",
        owner_user_id=owner_user_id,
    )


async def get_support_group_link(*, owner_user_id: int | None = None) -> str:
    return await get_setting_link(
        "support_group_link",
        settings.SUPPORT_GROUP_LINK or "",
        owner_user_id=owner_user_id,
    )


async def get_custom_link(*, owner_user_id: int | None = None) -> str:
    return await get_setting_link("custom_link", "", owner_user_id=owner_user_id)


def parse_log_channel_id(raw: str | int | None) -> int | None:
    """Parse a stored or env log channel id; treat empty/zero as unset."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text == "0":
        return None
    try:
        channel_id = int(text)
    except (TypeError, ValueError):
        return None
    if channel_id == 0:
        return None
    return channel_id


async def resolve_log_channel_id() -> int | None:
    """Return log channel id from DB ``log_channel_id``, else env ``LOG_CHANNEL_ID``."""
    try:
        raw = await settings_repo.get_bot_setting(LOG_CHANNEL_SETTING_KEY)
        parsed = parse_log_channel_id(raw)
        if parsed is not None:
            return parsed
    except Exception:
        logger.debug(
            "Failed to read %s from bot_settings; falling back to env",
            LOG_CHANNEL_SETTING_KEY,
            exc_info=True,
        )
    return parse_log_channel_id(settings.LOG_CHANNEL_ID)


async def _get_legacy_sudo_links() -> list[str]:
    raw = await settings_repo.get_bot_setting("sudo_links")
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    links: list[str] = []
    for item in parsed:
        if item is None:
            continue
        link = str(item).strip()
        if link:
            links.append(link)
    return links


async def get_sudo_buy_links() -> tuple[str, str]:
    raw_1 = await settings_repo.get_bot_setting("sudo_link_1")
    raw_2 = await settings_repo.get_bot_setting("sudo_link_2")
    if raw_1 is not None or raw_2 is not None:
        return _normalize(raw_1), _normalize(raw_2)

    legacy = await _get_legacy_sudo_links()
    if legacy:
        first = legacy[0] if len(legacy) > 0 else ""
        second = legacy[1] if len(legacy) > 1 else ""
        return first, second
    return "", ""


def _resolve_text_setting(
    lang: str,
    raw: str,
    fallback_key: str,
    **kwargs: object,
) -> str:
    parsed = decode_setting_value(raw)
    mode = parsed.get("mode")
    if mode == "text":
        value = str(parsed.get("value") or "").strip()
        if value:
            return _safe_format(value, **kwargs)
    if mode == "media":
        caption = str(parsed.get("caption") or "").strip()
        if caption:
            return _safe_format(caption, **kwargs)
    return t(lang, fallback_key, **kwargs)


async def get_start_welcome_text(
    lang: str,
    *,
    mention: str,
    owner_user_id: int | None = None,
    resolve_owner: bool = True,
) -> str:
    if owner_user_id is None and resolve_owner:
        owner_user_id = await resolve_single_active_owner_user_id()
    raw = await get_effective_setting_value(
        "start_text",
        owner_user_id=owner_user_id,
        env_default=settings.START_TEXT or "",
    )
    return _resolve_text_setting(
        lang,
        raw,
        "start.welcome",
        mention=mention,
    )


async def get_about_text(lang: str, *, owner_user_id: int | None = None) -> str:
    dev_link = await get_developer_link(owner_user_id=owner_user_id)
    raw = await get_effective_setting_value("about_text", owner_user_id=owner_user_id)
    return _resolve_text_setting(
        lang,
        raw,
        "start.about_text",
        developer_link=dev_link or "-",
    )


async def get_tariff_text(
    lang: str,
    *,
    base: str,
    music: str,
    video: str,
    owner_user_id: int | None = None,
) -> str:
    raw = await get_effective_setting_value("tariff_text", owner_user_id=owner_user_id)
    parsed = decode_setting_value(raw)
    mode = parsed.get("mode")
    if mode in ("text", "media"):
        template = str(parsed.get("value") or parsed.get("caption") or "").strip()
        if template:
            return _safe_format(
                template,
                base_rate=base,
                music_rate=music,
                video_rate=video,
            )

    entry_tmpl = t(lang, "list_fmt.setting_entry")
    lines = [
        t(lang, "start.menu.pricing"),
        "",
        entry_tmpl.format(key=t(lang, "panels.developer.set_base_rate"), value=base),
        entry_tmpl.format(key=t(lang, "panels.developer.set_music_rate"), value=music),
        entry_tmpl.format(key=t(lang, "panels.developer.set_video_rate"), value=video),
    ]
    return "\n".join(lines)


async def build_start_links(
    client: Client | None,
    *,
    owner_user_id: int | None = None,
) -> dict[str, str]:
    creator = await get_developer_link(owner_user_id=owner_user_id)
    sudo_1, sudo_2 = await get_sudo_buy_links()
    guide_channel = await get_guide_channel_link(owner_user_id=owner_user_id)
    bot_channel = await get_bot_channel_link(owner_user_id=owner_user_id)
    support_group = await get_support_group_link(owner_user_id=owner_user_id)
    custom_link = await get_custom_link(owner_user_id=owner_user_id)

    bot_username = ""
    if client is not None:
        try:
            me = await client.get_me()
            bot_username = me.username or ""
        except Exception:
            bot_username = ""

    add_to_group = f"https://t.me/{bot_username}?startgroup=true" if bot_username else ""
    add_to_channel = f"https://t.me/{bot_username}?startchannel=true" if bot_username else ""

    return {
        "creator": creator,
        "sudo_1": sudo_1,
        "sudo_2": sudo_2,
        "guide_channel": guide_channel,
        "bot_channel": bot_channel,
        "support_group": support_group,
        "custom_link": custom_link,
        "add_to_group": add_to_group,
        "add_to_channel": add_to_channel,
    }
