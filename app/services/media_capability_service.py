"""Central free-mode media capability checks for installed chats."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Literal

from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.services.install_policy_service import InstallPolicyService
from app.services import group_runtime_state_service
from app.utils.i18n import t

logger = logging.getLogger(__name__)

_MEDIA_FEATURES = frozenset({"audio", "download", "video", "tv", "radio", "satellite"})
BOT_MEDIA_SETTING_KEYS = {
    "audio": "owner_media_audio_enabled",
    "video": "owner_media_video_enabled",
    "file": "owner_media_file_enabled",
    "download": "owner_media_download_enabled",
    "buttons": "owner_media_buttons_enabled",
}


@dataclass(frozen=True)
class MediaCapabilities:
    """Effective media features for a chat install tier.

    Attributes:
        is_restricted: True when the chat is on free-install media tier.
        audio: Whether audio/music playback is allowed.
        download: Whether download commands are allowed.
        video: Whether video playback is allowed.
        tv: Whether TV streams are allowed.
        radio: Whether radio streams are allowed.
        satellite: Whether satellite streams are allowed.
    """

    is_restricted: bool
    audio: bool = True
    download: bool = True
    video: bool = True
    tv: bool = True
    radio: bool = True
    satellite: bool = True


_FULL_ACCESS = MediaCapabilities(is_restricted=False)
_FREE_ACCESS = MediaCapabilities(
    is_restricted=True,
    audio=True,
    download=True,
    video=False,
    tv=False,
    radio=True,
    satellite=False,
)


async def is_chat_free_media_restricted(chat_id: int) -> bool:
    """Return True when this chat is on free-install media tier.

    Free tier means install fee was zero under current policy signals and the
    chat has not received paid credit top-ups (``total_charged == 0``).

    Paid policy channel installs and any chat with paid credit keep full media.
    """
    state = await group_runtime_state_service.get_runtime_credit_state(chat_id, "group")
    if not state.has_runtime_credit:
        return False
    credit = state.credit
    if credit is None:
        return False

    if str(getattr(credit, "status", "") or "") == "unlimited":
        return False

    if credit.total_charged > 0:
        return False

    if await InstallPolicyService.is_free_install(chat_id):
        return True

    policy = await InstallPolicyService.get_policy()
    if policy is None:
        return False

    mode = policy.policy_mode
    if mode in ("free", "open"):
        return True

    if mode == "hybrid" and credit.chat_type == "group":
        return True

    return False


async def get_chat_media_capabilities(chat_id: int) -> MediaCapabilities:
    """Return allowed media features for a chat."""
    if await is_chat_free_media_restricted(chat_id):
        return _FREE_ACCESS
    return _FULL_ACCESS


async def _effective_capabilities(chat_id: int) -> MediaCapabilities:
    """Merge install-tier caps with per-chat video toggle."""
    caps = await get_chat_media_capabilities(chat_id)
    if await is_chat_video_enabled(chat_id):
        return caps
    return MediaCapabilities(
        is_restricted=caps.is_restricted,
        audio=caps.audio,
        download=caps.download,
        video=False,
        tv=caps.tv,
        radio=caps.radio,
        satellite=caps.satellite,
    )


async def build_playback_type_menu(lang: str, chat_id: int) -> InlineKeyboardMarkup:
    """Build a capability-aware playback type keyboard for a chat.

    Falls back to the legacy full menu when capability lookup fails.
    """
    from app.utils.ui import KeyboardFactory

    try:
        caps = await _effective_capabilities(chat_id)
        return KeyboardFactory.playback_type_menu(lang, capabilities=caps)
    except Exception:
        logger.exception(
            "Failed to resolve playback menu capabilities for chat %s; using full menu",
            chat_id,
        )
        return KeyboardFactory.playback_type_menu(lang)


async def build_now_playing_controls(lang: str, chat_id: int):
    """Build now-playing keyboard respecting per-chat buttons_enabled."""
    from app.services import CallService
    from app.utils.ui import KeyboardFactory

    return KeyboardFactory.now_playing_controls(
        lang,
        CallService.get_repeat_state(chat_id),
        show_extras=await is_chat_buttons_enabled(chat_id),
    )


def normalize_default_media_type(value: object | None) -> Literal["audio", "video"]:
    """Normalize persisted default media type to audio or video."""
    if str(value or "").strip().lower() == "video":
        return "video"
    return "audio"


async def get_chat_default_media_type(chat_id: int) -> Literal["audio", "video"]:
    """Return per-chat default playback media type for ambiguous play paths."""
    from app.repositories import settings_repo

    cs = await settings_repo.get_chat_settings(chat_id)
    if cs is None:
        return "audio"
    return normalize_default_media_type(getattr(cs, "default_media_type", None))


async def is_bot_media_feature_enabled(feature: str) -> bool:
    """Return bot-instance media toggle state for runtime-backed media features."""
    key = BOT_MEDIA_SETTING_KEYS.get(feature)
    if key is None:
        return True
    from app.repositories import settings_repo

    return await settings_repo.get_bot_setting_bool(key, default=True)


async def get_bot_media_feature_states() -> dict[str, bool]:
    """Return all bot-instance media toggle states."""
    states: dict[str, bool] = {}
    for feature in BOT_MEDIA_SETTING_KEYS:
        states[feature] = await is_bot_media_feature_enabled(feature)
    return states


async def is_chat_video_enabled(chat_id: int) -> bool:
    """Return whether per-chat video playback is enabled in group settings."""
    from app.repositories import settings_repo

    if not await is_bot_media_feature_enabled("video"):
        return False
    cs = await settings_repo.get_chat_settings(chat_id)
    if cs is None:
        return True
    return bool(getattr(cs, "video_enabled", True))


async def is_chat_buttons_enabled(chat_id: int) -> bool:
    """Return whether optional now-playing control buttons are enabled."""
    from app.repositories import settings_repo

    if not await is_bot_media_feature_enabled("buttons"):
        return False
    cs = await settings_repo.get_chat_settings(chat_id)
    if cs is None:
        return True
    return bool(getattr(cs, "buttons_enabled", True))


async def is_media_feature_allowed(chat_id: int, feature: str) -> bool:
    """Return whether a named media feature is allowed in this chat."""
    if feature not in _MEDIA_FEATURES:
        return True
    if feature in BOT_MEDIA_SETTING_KEYS and not await is_bot_media_feature_enabled(feature):
        return False
    caps = await get_chat_media_capabilities(chat_id)
    return bool(getattr(caps, feature, True))


def denial_message_key(feature: str) -> str:
    """Map a blocked feature to an i18n denial key."""
    if feature in ("tv", "satellite"):
        return "free_mode.blocked_tv_satellite"
    if feature == "video":
        return "free_mode.blocked_video"
    return "free_mode.blocked_generic"


def feature_from_media_type(media_type: str) -> str:
    """Map playback media type to capability feature name."""
    if media_type == "video":
        return "video"
    return "audio"


async def deny_video_playback(
    target: Message | CallbackQuery,
    *,
    lang: str = "fa",
) -> bool:
    """Deny video when disabled for the chat or blocked by free-mode tier.

    Free-mode tier denial still applies when per-chat video is enabled.

    Returns:
        True when playback must stop (message already sent). False when allowed.
    """
    chat_id = target.message.chat.id
    if not await is_chat_video_enabled(chat_id):
        text = t(lang, "playback_cmd.video_disabled_in_group")
        if isinstance(target, CallbackQuery):
            await target.answer(text, show_alert=True)
        else:
            await target.reply(text)
        logger.info("Group video disabled for chat %s", chat_id)
        return True

    if await is_media_feature_allowed(chat_id, "video"):
        return False

    text = t(lang, denial_message_key("video"))
    if isinstance(target, CallbackQuery):
        await target.answer(text, show_alert=True)
    else:
        await target.reply(text)
    logger.info("Free-mode denied video for chat %s", chat_id)
    return True


async def deny_free_mode_media(
    target: Message | CallbackQuery,
    feature: str,
    *,
    lang: str = "fa",
) -> bool:
    """Send a localized denial when a feature is blocked.

    Args:
        target: Message or callback query to reply to.
        feature: Capability feature name.
        lang: Language code for i18n.

    Returns:
        True when the feature was denied (caller must stop). False when allowed.
    """
    message = target.message if isinstance(target, CallbackQuery) else target
    chat_id = message.chat.id
    if await is_media_feature_allowed(chat_id, feature):
        return False

    text = t(lang, denial_message_key(feature))
    if isinstance(target, CallbackQuery):
        await target.answer(text, show_alert=True)
    else:
        await target.reply(text)
    logger.info("Free-mode denied %s for chat %s", feature, chat_id)
    return True
