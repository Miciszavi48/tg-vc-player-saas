from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.handlers.priority import PRIORITY_COMMAND_GROUP
from app.services import CallService
from app.services.language_service import resolve_lang
from app.services.now_playing_renderer import (
    NowPlayingContext,
    render_now_playing_text,
    requester_display_name,
    safe_track_id,
)
from app.utils.i18n import AUTO_LANG, t
from app.utils.media_sources import validate_safe_url_with_redirects
from app.services.media_capability_service import build_now_playing_controls, deny_free_mode_media
from app.services.playback_dispatch_service import (
    apply_busy_playback_decision,
    decide_busy_playback,
)
from app.utils.playback_auth import authorize_playback_action
from app.utils.playback_errors import edit_playback_join_failure
from app.utils.telegram_message import safe_edit_message
from app.utils.ui import CB, KeyboardFactory

logger = logging.getLogger(__name__)

_LANG = AUTO_LANG
_TV_CHANNELS_PATH = Path(__file__).resolve().parent.parent / "assets" / "tv_channels.json"
_RADIO_STATIONS_PATH = Path(__file__).resolve().parent.parent / "assets" / "radio_stations.json"
_SAT_CHANNELS_PATH = Path(__file__).resolve().parent.parent / "assets" / "satellite_channels.json"
_CHANNEL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SAT_PAGE_SIZE = 8
_RADIO_TEXT_CMDS = ["پخش رادیو", "Radio Play"]
_SATELLITE_TEXT_CMDS = ["پخش ماهواره", "Satellite Play"]
# CONTENT-06: the evidenced "thematic world-channel" browse is exactly a
# topic-first walk of the world-channel catalog, which is the satellite catalog
# with its `group` taxonomy (CONTENT-03). This is an additional trigger into
# that shared surface, not a second isolated catalog flow.
_THEMATIC_TEXT_CMDS = ["پخش موضوعی", "Thematic Play"]
_PLAYBACK_RESULT_FALLBACK_SENT: set[tuple[int | None, int | None, str]] = set()
_PLAYBACK_RESULT_FALLBACK_LIMIT = 512


def _parse_channel_id(data: str, prefix: str) -> str | None:
    if not data.startswith(prefix):
        return None
    value = data[len(prefix):].strip()
    if not value or not _CHANNEL_ID_RE.fullmatch(value):
        return None
    return value


def _parse_page(data: str, prefix: str) -> int | None:
    if not data.startswith(prefix):
        return None
    raw = data[len(prefix):].strip()
    if not raw.isdigit():
        return None
    page = int(raw)
    if page < 0 or page > 10_000:
        return None
    return page


def _load_json(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return []
    except Exception:
        logger.exception("Failed to load %s", path)
        return []


def _message_text(message: Message) -> str:
    return str(getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()


def _matches_exact_text_command(text: str | None, commands: list[str]) -> bool:
    normalized = str(text or "").strip()
    return any(normalized.casefold() == command.casefold() for command in commands)


def _exact_text_command_filter(commands: list[str]):
    async def func(_flt, _client, message: Message) -> bool:
        return _matches_exact_text_command(_message_text(message), commands)

    return filters.create(func, name="ExactTvRadioTextCommandFilter")


def _usable_stream_entries(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    usable: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_id = str(entry.get("id") or "").strip()
        name = str(entry.get("name") or "").strip()
        url = str(entry.get("url") or "").strip()
        if not entry_id or not name or not url:
            continue
        if not _CHANNEL_ID_RE.fullmatch(entry_id):
            continue
        cleaned = dict(entry)
        cleaned["id"] = entry_id
        cleaned["name"] = name
        cleaned["url"] = url
        usable.append(cleaned)
    return usable


def _radio_stations_menu(lang: str, stations: list[dict[str, str]]) -> InlineKeyboardMarkup:
    """Flat station list. Superseded by the country step (CONTENT-05); retained
    so any stale keyboard still renders the same way it used to."""
    rows = [[InlineKeyboardButton(s["name"], callback_data=f"pb:radio:{s['id']}")] for s in stations]
    rows.append([InlineKeyboardButton(t(lang, "common.buttons.back"), callback_data=CB["NAV_BACK"])])
    rows.append([InlineKeyboardButton(t(lang, "common.buttons.home"), callback_data=CB["WZ_HOME"])])
    return InlineKeyboardMarkup(rows)


_GROUP_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SAT_GROUP_RE = re.compile(r"^pb:sat:g:(?P<slug>[a-z0-9_]{1,40})(?::p:(?P<page>\d{1,3}))?$")
_RADIO_GROUP_RE = re.compile(r"^pb:radio:c:(?P<slug>[a-z0-9_]{1,40})$")


def slugify_catalog_group(value: str) -> str:
    """Reduce a catalog group/country value to callback-data-safe ASCII.

    The satellite catalog carries values like ``classic;music`` which cannot go
    into callback data verbatim, so every group is addressed by its slug.
    """
    slug = _GROUP_SLUG_RE.sub("_", str(value or "").strip().lower()).strip("_")
    return slug or "other"


def group_catalog_entries(
    entries: list[dict[str, str]], field: str,
) -> dict[str, list[dict[str, str]]]:
    """Bucket catalog entries by the slug of ``field`` (CONTENT-03/05/06).

    Entries missing the field fall into the ``other`` bucket so no channel ever
    becomes unreachable just because its metadata is incomplete.
    """
    buckets: dict[str, list[dict[str, str]]] = {}
    for entry in entries:
        slug = slugify_catalog_group(entry.get(field, ""))
        buckets.setdefault(slug, []).append(entry)
    return dict(sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0])))


def catalog_group_label(lang: str, prefix: str, slug: str) -> str:
    """Localized label for a catalog group, falling back to the raw slug."""
    key = f"tv_radio.{prefix}_{slug}"
    label = t(lang, key)
    # t() returns "[missing:<key>]" for an unknown key; show the slug instead so
    # a catalog gaining a new group never renders a debug marker to users.
    if not label or label.startswith("[missing:") or label.startswith("[invalid:"):
        return slug.replace("_", " ").title()
    return label


def _catalog_group_rows(
    entries: list[dict[str, str]], field: str, label_prefix: str, lang: str,
) -> list[tuple[str, str, int]]:
    """Build ``(slug, label, count)`` rows for a catalog group menu."""
    return [
        (slug, catalog_group_label(lang, label_prefix, slug), len(items))
        for slug, items in group_catalog_entries(entries, field).items()
    ]


def _satellite_group_kb(channels: list[dict[str, str]]) -> InlineKeyboardMarkup:
    return KeyboardFactory.catalog_group_menu(
        _LANG,
        _catalog_group_rows(channels, "group", "sat_group", _LANG),
        callback_prefix="pb:sat:g:",
    )


def _radio_group_kb(stations: list[dict[str, str]]) -> InlineKeyboardMarkup:
    return KeyboardFactory.catalog_group_menu(
        _LANG,
        _catalog_group_rows(stations, "country", "radio_group", _LANG),
        callback_prefix="pb:radio:c:",
    )


def _nav_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(t(lang, "common.buttons.back"), callback_data=CB["NAV_BACK"])],
            [InlineKeyboardButton(t(lang, "common.buttons.home"), callback_data=CB["WZ_HOME"])],
        ]
    )


async def _render_playing_message(
    chat_id: int,
    *,
    title: str | None,
    media_type: str,
    user: object | None,
    stream_id: str | None = None,
) -> tuple[str, str]:
    """Build localized now-playing text and resolved language for a stream play."""
    user_id = getattr(user, "id", None)
    lang = await resolve_lang(chat_id=chat_id, user_id=user_id)
    text = await render_now_playing_text(
        chat_id,
        NowPlayingContext(
            title=title,
            media_type=media_type,
            requester_name=requester_display_name(user),
            requester_id=user_id,
            track_id=safe_track_id(stream_id),
        ),
        lang=lang,
        user_id=user_id,
    )
    return text, lang


async def _playing_kb(lang: str, chat_id: int) -> InlineKeyboardMarkup:
    controls = await build_now_playing_controls(lang, chat_id)
    rows = [list(row) for row in controls.inline_keyboard]
    rows.append([InlineKeyboardButton(t(lang, "common.buttons.back"), callback_data=CB["NAV_BACK"])])
    rows.append([InlineKeyboardButton(t(lang, "common.buttons.home"), callback_data=CB["WZ_HOME"])])
    return InlineKeyboardMarkup(rows)


def _fallback_key(query: CallbackQuery) -> tuple[int | None, int | None, str]:
    message = getattr(query, "message", None)
    chat = getattr(message, "chat", None) if message is not None else None
    message_id = (
        getattr(message, "id", None)
        or getattr(message, "message_id", None)
        if message is not None
        else None
    )
    return (getattr(chat, "id", None), message_id, str(getattr(query, "data", "") or ""))


def _remember_fallback(key: tuple[int | None, int | None, str]) -> None:
    if len(_PLAYBACK_RESULT_FALLBACK_SENT) >= _PLAYBACK_RESULT_FALLBACK_LIMIT:
        _PLAYBACK_RESULT_FALLBACK_SENT.clear()
    _PLAYBACK_RESULT_FALLBACK_SENT.add(key)


async def _safe_edit_stream_playback_result(
    client: Client,
    query: CallbackQuery,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> str:
    message = getattr(query, "message", None)
    if message is not None and await safe_edit_message(message, text, reply_markup=reply_markup):
        return "edited"

    key = _fallback_key(query)
    if key in _PLAYBACK_RESULT_FALLBACK_SENT:
        return "fallback_skipped"

    if message is not None:
        reply = getattr(message, "reply", None)
        if callable(reply):
            try:
                await reply(text, reply_markup=reply_markup)
                _remember_fallback(key)
                return "fallback_sent"
            except Exception:
                logger.debug("stream playback fallback reply failed", exc_info=True)

    chat_id = key[0]
    send_message = getattr(client, "send_message", None) if client is not None else None
    if callable(send_message) and chat_id is not None:
        try:
            await send_message(chat_id, text, reply_markup=reply_markup)
            _remember_fallback(key)
            return "fallback_sent"
        except Exception:
            logger.debug("stream playback fallback send failed", exc_info=True)

    return "failed"


async def show_tv_menu(client: Client, message: Message) -> None:
    """Display TV channel selection keyboard (called from playback handler)."""
    channels = _load_json(_TV_CHANNELS_PATH)
    if not channels:
        await message.reply(
            t(_LANG, "tv_radio.no_channels"),
            reply_markup=_nav_kb(_LANG),
        )
        return
    await message.reply(
        t(_LANG, "tv_radio.choose_channel"),
        reply_markup=KeyboardFactory.tv_channels_menu(_LANG, channels),
    )


async def show_radio_menu(client: Client, message: Message) -> None:
    """Display the radio station selection keyboard from a text command."""
    if not await authorize_playback_action(client, message, lang=_LANG):
        return
    stations = _usable_stream_entries(_load_json(_RADIO_STATIONS_PATH))
    if not stations:
        await message.reply(
            t(_LANG, "tv_radio.no_channels"),
            reply_markup=_nav_kb(_LANG),
        )
        return
    await message.reply(
        t(_LANG, "tv_radio.choose_radio_group"),
        reply_markup=_radio_group_kb(stations),
    )


async def show_satellite_menu(client: Client, message: Message) -> None:
    """Display the satellite channel selection keyboard from a text command."""
    if not await authorize_playback_action(client, message, lang=_LANG):
        return
    if await deny_free_mode_media(message, "satellite", lang=_LANG):
        return
    channels = _usable_stream_entries(_load_json(_SAT_CHANNELS_PATH))
    if not channels:
        await message.reply(
            t(_LANG, "tv_radio.no_satellite"),
            reply_markup=_nav_kb(_LANG),
        )
        return
    await message.reply(
        t(_LANG, "tv_radio.choose_sat_group"),
        reply_markup=_satellite_group_kb(channels),
    )


def register(bot: Client, call_py) -> None:
    @bot.on_message(
        _exact_text_command_filter(_RADIO_TEXT_CMDS) & filters.group,
        group=PRIORITY_COMMAND_GROUP,
    )
    async def play_radio_menu(client: Client, message: Message):
        await show_radio_menu(client, message)

    @bot.on_message(
        _exact_text_command_filter(_SATELLITE_TEXT_CMDS) & filters.group,
        group=PRIORITY_COMMAND_GROUP,
    )
    async def play_satellite_menu(client: Client, message: Message):
        await show_satellite_menu(client, message)

    @bot.on_message(
        _exact_text_command_filter(_THEMATIC_TEXT_CMDS) & filters.group,
        group=PRIORITY_COMMAND_GROUP,
    )
    async def play_thematic_menu(client: Client, message: Message):
        """CONTENT-06: topic-first browse of the world-channel catalog."""
        await show_satellite_menu(client, message)

    # ── Satellite TV ──────────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['PB_SAT']}$"))
    async def on_satellite_menu(client: Client, query: CallbackQuery):
        if not await authorize_playback_action(client, query, lang=_LANG):
            return
        if await deny_free_mode_media(query, "satellite", lang=_LANG):
            return
        await query.answer()
        channels = _usable_stream_entries(_load_json(_SAT_CHANNELS_PATH))
        if not channels:
            await _safe_edit_stream_playback_result(
                client,
                query,
                t(_LANG, "tv_radio.no_satellite"),
                reply_markup=_nav_kb(_LANG),
            )
            return
        await _safe_edit_stream_playback_result(
            client,
            query,
            t(_LANG, "tv_radio.choose_sat_group"),
            reply_markup=_satellite_group_kb(channels),
        )

    @bot.on_callback_query(filters.regex(r"^pb:sat:page:"))
    async def on_satellite_page(client: Client, query: CallbackQuery):
        page = _parse_page(query.data, "pb:sat:page:")
        if page is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        if not await authorize_playback_action(client, query, lang=_LANG):
            return
        channels = _usable_stream_entries(_load_json(_SAT_CHANNELS_PATH))
        if not channels:
            await query.answer()
            await _safe_edit_stream_playback_result(
                client,
                query,
                t(_LANG, "tv_radio.no_satellite"),
                reply_markup=_nav_kb(_LANG),
            )
            return
        total_pages = max(1, (len(channels) + _SAT_PAGE_SIZE - 1) // _SAT_PAGE_SIZE)
        if page >= total_pages:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer()
        await _safe_edit_stream_playback_result(
            client,
            query,
            t(_LANG, "tv_radio.choose_satellite"),
            reply_markup=KeyboardFactory.satellite_channels_menu(_LANG, channels, page=page, per_page=_SAT_PAGE_SIZE),
        )

    @bot.on_callback_query(filters.regex(_SAT_GROUP_RE))
    async def on_satellite_group(client: Client, query: CallbackQuery):
        """CONTENT-03: show one satellite topic group, paginated."""
        match = _SAT_GROUP_RE.match(str(getattr(query, "data", "") or ""))
        if match is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        if not await authorize_playback_action(client, query, lang=_LANG):
            return
        if await deny_free_mode_media(query, "satellite", lang=_LANG):
            return
        await query.answer()
        slug = match.group("slug")
        page = int(match.group("page") or 0)
        buckets = group_catalog_entries(
            _usable_stream_entries(_load_json(_SAT_CHANNELS_PATH)), "group"
        )
        channels = buckets.get(slug)
        if not channels:
            await _safe_edit_stream_playback_result(
                client,
                query,
                t(_LANG, "tv_radio.group_empty"),
                reply_markup=_nav_kb(_LANG),
            )
            return
        await _safe_edit_stream_playback_result(
            client,
            query,
            t(_LANG, "tv_radio.choose_satellite"),
            reply_markup=KeyboardFactory.satellite_group_channels_menu(
                _LANG, channels, slug, page=page, per_page=_SAT_PAGE_SIZE,
            ),
        )

    @bot.on_callback_query(filters.regex(r"^pb:sat:(?!page:|g:)"))
    async def on_satellite_select(client: Client, query: CallbackQuery):
        channel_id = _parse_channel_id(query.data, "pb:sat:")
        if channel_id is None:
            await query.answer()
            await _safe_edit_stream_playback_result(
                client,
                query,
                t(_LANG, "common.errors.try_later"),
                reply_markup=_nav_kb(_LANG),
            )
            return
        channels = _load_json(_SAT_CHANNELS_PATH)
        selected = next((c for c in channels if c.get("id") == channel_id), None)
        if selected is None:
            await query.answer()
            await _safe_edit_stream_playback_result(
                client,
                query,
                t(_LANG, "tv_radio.no_satellite"),
                reply_markup=_nav_kb(_LANG),
            )
            return
        stream_url = selected.get("url", "")
        if not stream_url:
            await query.answer()
            await _safe_edit_stream_playback_result(
                client,
                query,
                t(_LANG, "playback_cmd.failed"),
                reply_markup=_nav_kb(_LANG),
            )
            return
        if not await authorize_playback_action(client, query, lang=_LANG):
            return
        if await deny_free_mode_media(query, "satellite", lang=_LANG):
            return
        await query.answer()
        stream_url = await validate_safe_url_with_redirects(stream_url)
        if stream_url is None:
            await _safe_edit_stream_playback_result(
                client,
                query,
                t(_LANG, "playback_cmd.blocked_url"),
                reply_markup=_nav_kb(_LANG),
            )
            return
        chat_id = query.message.chat.id
        media_type = "video"
        busy_decision = await decide_busy_playback(
            chat_id,
            stream_url,
            media_type,
            is_temp_local=False,
        )
        if await apply_busy_playback_decision(
            query,
            chat_id,
            stream_url,
            media_type,
            busy_decision,
            title=selected.get("name"),
            requester_id=query.from_user.id if query.from_user else None,
            lang=_LANG,
        ):
            if busy_decision.message_key:
                await _safe_edit_stream_playback_result(
                    client,
                    query,
                    t(_LANG, busy_decision.message_key),
                    reply_markup=_nav_kb(_LANG),
                )
            return

        ok = await CallService.join_voice_chat(
            call_py,
            chat_id,
            stream_url,
            media_type,
            user_id=query.from_user.id if query.from_user else None,
            event_source=stream_url,
            title=selected.get("name"),
            playback_feature="satellite",
        )
        if ok:
            text, lang = await _render_playing_message(
                chat_id,
                title=selected.get("name", channel_id),
                media_type="satellite",
                user=query.from_user,
                stream_id=channel_id,
            )
            await _safe_edit_stream_playback_result(
                client,
                query,
                text,
                reply_markup=await _playing_kb(lang, chat_id),
            )
        else:
            await edit_playback_join_failure(
                query,
                lang=_LANG,
                reply_markup=_nav_kb(_LANG),
            )

    # ── TV channel selection callback ─────────────────────────────────────
    @bot.on_callback_query(filters.regex(r"^pb:tv:"))
    async def on_tv_select(client: Client, query: CallbackQuery):
        channel_id = _parse_channel_id(query.data, "pb:tv:")
        if channel_id is None:
            await query.answer()
            await _safe_edit_stream_playback_result(
                client,
                query,
                t(_LANG, "common.errors.try_later"),
                reply_markup=_nav_kb(_LANG),
            )
            return
        channels = _load_json(_TV_CHANNELS_PATH)
        selected = next((c for c in channels if c.get("id") == channel_id), None)
        if selected is None:
            await query.answer()
            await _safe_edit_stream_playback_result(
                client,
                query,
                t(_LANG, "tv_radio.no_channels"),
                reply_markup=_nav_kb(_LANG),
            )
            return

        stream_url = selected.get("url", "")
        if not stream_url:
            await query.answer()
            await _safe_edit_stream_playback_result(
                client,
                query,
                t(_LANG, "playback_cmd.failed"),
                reply_markup=_nav_kb(_LANG),
            )
            return
        if not await authorize_playback_action(client, query, lang=_LANG):
            return
        if await deny_free_mode_media(query, "tv", lang=_LANG):
            return
        await query.answer()
        stream_url = await validate_safe_url_with_redirects(stream_url)
        if stream_url is None:
            await _safe_edit_stream_playback_result(
                client,
                query,
                t(_LANG, "playback_cmd.blocked_url"),
                reply_markup=_nav_kb(_LANG),
            )
            return

        chat_id = query.message.chat.id
        media_type = "video"
        busy_decision = await decide_busy_playback(
            chat_id,
            stream_url,
            media_type,
            is_temp_local=False,
        )
        if await apply_busy_playback_decision(
            query,
            chat_id,
            stream_url,
            media_type,
            busy_decision,
            title=selected.get("name"),
            requester_id=query.from_user.id if query.from_user else None,
            lang=_LANG,
        ):
            if busy_decision.message_key:
                await _safe_edit_stream_playback_result(
                    client,
                    query,
                    t(_LANG, busy_decision.message_key),
                    reply_markup=_nav_kb(_LANG),
                )
            return

        ok = await CallService.join_voice_chat(
            call_py,
            chat_id,
            stream_url,
            media_type,
            user_id=query.from_user.id if query.from_user else None,
            event_source=stream_url,
            title=selected.get("name"),
            playback_feature="tv",
        )
        if ok:
            text, lang = await _render_playing_message(
                chat_id,
                title=selected.get("name", channel_id),
                media_type="tv",
                user=query.from_user,
                stream_id=channel_id,
            )
            await _safe_edit_stream_playback_result(
                client,
                query,
                text,
                reply_markup=await _playing_kb(lang, chat_id),
            )
        else:
            await edit_playback_join_failure(
                query,
                lang=_LANG,
                reply_markup=_nav_kb(_LANG),
            )

    # ── Radio selection ───────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['PB_RADIO']}$"))
    async def on_radio_menu(client: Client, query: CallbackQuery):
        if not await authorize_playback_action(client, query, lang=_LANG):
            return
        await query.answer()
        stations = _usable_stream_entries(_load_json(_RADIO_STATIONS_PATH))
        if not stations:
            await _safe_edit_stream_playback_result(
                client,
                query,
                t(_LANG, "tv_radio.no_channels"),
                reply_markup=_nav_kb(_LANG),
            )
            return

        await _safe_edit_stream_playback_result(
            client,
            query,
            t(_LANG, "tv_radio.choose_radio_group"),
            reply_markup=_radio_group_kb(stations),
        )

    @bot.on_callback_query(filters.regex(_RADIO_GROUP_RE))
    async def on_radio_group(client: Client, query: CallbackQuery):
        """CONTENT-05: show radio stations for one language/country group."""
        match = _RADIO_GROUP_RE.match(str(getattr(query, "data", "") or ""))
        if match is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        if not await authorize_playback_action(client, query, lang=_LANG):
            return
        await query.answer()
        buckets = group_catalog_entries(
            _usable_stream_entries(_load_json(_RADIO_STATIONS_PATH)), "country"
        )
        stations = buckets.get(match.group("slug"))
        if not stations:
            await _safe_edit_stream_playback_result(
                client,
                query,
                t(_LANG, "tv_radio.group_empty"),
                reply_markup=_nav_kb(_LANG),
            )
            return
        await _safe_edit_stream_playback_result(
            client,
            query,
            t(_LANG, "tv_radio.choose_radio"),
            reply_markup=KeyboardFactory.radio_group_stations_menu(_LANG, stations),
        )

    @bot.on_callback_query(filters.regex(r"^pb:radio:(?!c:)"))
    async def on_radio_select(client: Client, query: CallbackQuery):
        station_id = _parse_channel_id(query.data, "pb:radio:")
        if station_id is None:
            await query.answer()
            await _safe_edit_stream_playback_result(
                client,
                query,
                t(_LANG, "common.errors.try_later"),
                reply_markup=_nav_kb(_LANG),
            )
            return
        stations = _load_json(_RADIO_STATIONS_PATH)
        selected = next((s for s in stations if s.get("id") == station_id), None)
        if selected is None:
            await query.answer()
            await _safe_edit_stream_playback_result(
                client,
                query,
                t(_LANG, "tv_radio.no_channels"),
                reply_markup=_nav_kb(_LANG),
            )
            return

        stream_url = selected.get("url", "")
        if not stream_url:
            await query.answer()
            await _safe_edit_stream_playback_result(
                client,
                query,
                t(_LANG, "playback_cmd.failed"),
                reply_markup=_nav_kb(_LANG),
            )
            return
        if not await authorize_playback_action(client, query, lang=_LANG):
            return
        await query.answer()
        stream_url = await validate_safe_url_with_redirects(stream_url)
        if stream_url is None:
            await _safe_edit_stream_playback_result(
                client,
                query,
                t(_LANG, "playback_cmd.blocked_url"),
                reply_markup=_nav_kb(_LANG),
            )
            return

        chat_id = query.message.chat.id
        media_type = "audio"
        busy_decision = await decide_busy_playback(
            chat_id,
            stream_url,
            media_type,
            is_temp_local=False,
        )
        if await apply_busy_playback_decision(
            query,
            chat_id,
            stream_url,
            media_type,
            busy_decision,
            title=selected.get("name"),
            requester_id=query.from_user.id if query.from_user else None,
            lang=_LANG,
        ):
            if busy_decision.message_key:
                await _safe_edit_stream_playback_result(
                    client,
                    query,
                    t(_LANG, busy_decision.message_key),
                    reply_markup=_nav_kb(_LANG),
                )
            return

        ok = await CallService.join_voice_chat(
            call_py,
            chat_id,
            stream_url,
            media_type,
            user_id=query.from_user.id if query.from_user else None,
            event_source=stream_url,
            title=selected.get("name"),
            playback_feature="radio",
        )
        if ok:
            text, lang = await _render_playing_message(
                chat_id,
                title=selected.get("name", station_id),
                media_type="radio",
                user=query.from_user,
                stream_id=station_id,
            )
            await _safe_edit_stream_playback_result(
                client,
                query,
                text,
                reply_markup=await _playing_kb(lang, chat_id),
            )
        else:
            await edit_playback_join_failure(
                query,
                lang=_LANG,
                reply_markup=_nav_kb(_LANG),
            )
