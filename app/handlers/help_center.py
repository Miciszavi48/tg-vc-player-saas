"""Help Center: reference-complete Guide UI with user-bound callbacks."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import re

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message

from app.handlers.priority import HELP_CALLBACK_GROUP, PRIORITY_COMMAND_GROUP
from app.repositories import admin_repo, user_repo
from app.services.panel_message_service import panel_callback_edit
from app.utils.bot_guards import deny_if_sudo_panel_disabled, is_developer
from app.utils.i18n import AUTO_LANG, t
from app.utils.text_commands import help_command_filter
from app.utils.ui import CB, KeyboardFactory

logger = logging.getLogger(__name__)

_LANG = AUTO_LANG
_HELP_BOUND_RE = re.compile(r"^(?P<page>h:[A-Za-z0-9_:]+):U(?P<user_id>\d+)$")

# String regex (same kurigram dispatch path as working grp:* handlers).
HELP_CALLBACK_PATTERN = (
    r"^h:(?:home|play|public|promote|close|utility|"
    r"play:(?:reply|link|auto_music|auto_video|youtube|radio|serial|tv|satellite|controls)|"
    r"public:(?:group|user)|promote:(?:deputy|admin|vip)):U\d+$"
)
_HELP_CALLBACK_PATTERN_RE = re.compile(HELP_CALLBACK_PATTERN)

# Explicit Kurigram string-regex routes (same style as working ``grp:*`` handlers).
HELP_ROUTE_SPECS: tuple[tuple[str, str], ...] = (
    ("help_route_home", r"^h:home:U\d+$"),
    ("help_route_play", r"^h:play:U\d+$"),
    ("help_route_public", r"^h:public:U\d+$"),
    ("help_route_promote", r"^h:promote:U\d+$"),
    ("help_route_utility", r"^h:utility:U\d+$"),
    ("help_route_close", r"^h:close:U\d+$"),
    (
        "help_route_play_detail",
        r"^h:play:(?:reply|link|auto_music|auto_video|youtube|radio|serial|tv|satellite|controls):U\d+$",
    ),
    ("help_route_public_detail", r"^h:public:(?:group|user):U\d+$"),
    (
        "help_route_promote_detail",
        r"^h:promote:(?:deputy|admin|vip):U\d+$",
    ),
    ("help_route_legacy", HELP_CALLBACK_PATTERN),
)

_ADMIN_SECTIONS = {
    CB["HELP_GROUP_PANEL"]: ("group_panel", {"developer", "owner", "sudo", "player_owner", "player_deputy", "music_admin", "video_admin"}),
    CB["HELP_SUDO_PANEL"]: ("sudo_panel", {"developer", "owner", "sudo"}),
    CB["HELP_OWNER_PANEL"]: ("owner_panel", {"developer", "owner"}),
    CB["HELP_DEV_PANEL"]: ("dev_panel", {"developer"}),
}

_LEGACY_PUBLIC_SECTIONS = {
    CB["HELP_GETTING_STARTED"]: "getting_started",
    CB["HELP_CONTROLS"]: "controls",
    CB["HELP_PLAYLIST"]: "playlist",
    CB["HELP_RADIO"]: "radio",
    CB["HELP_DOWNLOADS"]: "downloads",
    CB["HELP_FORCEJOIN"]: "forcejoin",
    CB["HELP_TROUBLESHOOT"]: "troubleshoot",
    CB["HELP_ABOUT"]: "about",
    CB["HELP_GROUP_COMMANDS"]: "group_commands",
    CB["HELP_CALL_COMMANDS"]: "call_commands",
    CB["HELP_MANAGER_COMMANDS"]: "manager_commands",
    CB["HELP_PRIVATE_COMMANDS"]: "private_commands",
}

_DETAIL_PAGES = {
    CB["HELP_PLAY_REPLY"]: ("play_reply", CB["HELP_PLAYBACK"]),
    CB["HELP_PLAY_LINK"]: ("play_link", CB["HELP_PLAYBACK"]),
    CB["HELP_PLAY_AUTO_MUSIC"]: ("play_auto_music", CB["HELP_PLAYBACK"]),
    CB["HELP_PLAY_AUTO_VIDEO"]: ("play_auto_video", CB["HELP_PLAYBACK"]),
    CB["HELP_PLAY_YOUTUBE"]: ("play_youtube", CB["HELP_PLAYBACK"]),
    CB["HELP_PLAY_RADIO"]: ("play_radio", CB["HELP_PLAYBACK"]),
    CB["HELP_PLAY_SERIAL"]: ("play_serial", CB["HELP_PLAYBACK"]),
    CB["HELP_PLAY_TV"]: ("play_tv", CB["HELP_PLAYBACK"]),
    CB["HELP_PLAY_SATELLITE"]: ("play_satellite", CB["HELP_PLAYBACK"]),
    CB["HELP_PLAY_CONTROLS"]: ("play_controls", CB["HELP_PLAYBACK"]),
    CB["HELP_PUBLIC_GROUP"]: ("public_group", CB["HELP_PUBLIC"]),
    CB["HELP_PUBLIC_USER"]: ("public_user", CB["HELP_PUBLIC"]),
    CB["HELP_PROMOTE_DEPUTY"]: ("promote_deputy", CB["HELP_PROMOTE"]),
    CB["HELP_PROMOTE_ADMIN"]: ("promote_admin", CB["HELP_PROMOTE"]),
    CB["HELP_PROMOTE_VIP"]: ("promote_vip", CB["HELP_PROMOTE"]),
    CB["HELP_UTILITY"]: ("utility", CB["HELP_HOME"]),
}

REFERENCE_HELP_PAGES = frozenset({
    CB["HELP_HOME"],
    CB["HELP_PLAYBACK"],
    CB["HELP_PUBLIC"],
    CB["HELP_PROMOTE"],
    *list(_DETAIL_PAGES),
})

_KNOWN_HELP_PAGES = frozenset({
    CB["HELP_CLOSE"],
    *REFERENCE_HELP_PAGES,
    *list(_LEGACY_PUBLIC_SECTIONS),
    *list(_ADMIN_SECTIONS),
})


@dataclass(frozen=True)
class HelpCallback:
    page: str
    bound_user_id: int | None


def parse_help_callback(data: str | None) -> HelpCallback | None:
    """Parse project-native Help callbacks, including optional :U binding."""
    if not data or not data.startswith("h:"):
        return None

    bound = _HELP_BOUND_RE.fullmatch(data)
    if bound:
        page = bound.group("page")
        if page not in _KNOWN_HELP_PAGES:
            return HelpCallback(page=page, bound_user_id=int(bound.group("user_id")))
        return HelpCallback(page=page, bound_user_id=int(bound.group("user_id")))

    if ":U" in data:
        return None

    return HelpCallback(page=data, bound_user_id=None)


def _callback_context(query: CallbackQuery) -> tuple[str | None, int | None, int | None, str]:
    chat = query.message.chat if query.message else None
    chat_type_raw = getattr(chat, "type", None) if chat else None
    chat_type = getattr(chat_type_raw, "value", chat_type_raw)
    message_chat_id = chat.id if chat else None
    user_id = query.from_user.id if query.from_user else None
    return chat_type, user_id, message_chat_id, query.data or ""


async def _answer_callback(
    query: CallbackQuery,
    text: str | None = None,
    *,
    show_alert: bool = False,
) -> None:
    try:
        if text is None:
            await query.answer()
        else:
            await query.answer(text, show_alert=show_alert)
    except Exception:
        chat_type, user_id, message_chat_id, data = _callback_context(query)
        logger.debug(
            "callback answer failed selected_handler=help data=%s chat_type=%s "
            "user_id=%s message_chat_id=%s",
            data,
            chat_type,
            user_id,
            message_chat_id,
            exc_info=True,
        )


async def _safe_help_edit(
    client: Client,
    query: CallbackQuery,
    text: str,
    *,
    reply_markup=None,
) -> bool:
    ok = await panel_callback_edit(client, query, text, reply_markup)
    if ok:
        return True
    chat_type, user_id, message_chat_id, data = _callback_context(query)
    logger.warning(
        "callback edit result selected_handler=help result=fallback_failed data=%s "
        "chat_type=%s user_id=%s message_chat_id=%s",
        data,
        chat_type,
        user_id,
        message_chat_id,
    )
    await _answer_callback(
        query,
        t(_LANG, "common.errors.navigation_failed"),
        show_alert=True,
    )
    return False


async def _safe_help_close(query: CallbackQuery) -> None:
    await _answer_callback(query)
    message = query.message
    delete = getattr(message, "delete", None) if message is not None else None
    if not callable(delete):
        return
    try:
        await delete()
    except Exception:
        chat_type, user_id, message_chat_id, data = _callback_context(query)
        logger.debug(
            "help close delete failed data=%s chat_type=%s user_id=%s message_chat_id=%s",
            data,
            chat_type,
            user_id,
            message_chat_id,
            exc_info=True,
        )


async def _get_role(user_id: int, chat_id: int | None = None) -> str:
    """Detect effective role (highest wins)."""
    if is_developer(user_id):
        return "developer"
    if await user_repo.is_owner(user_id):
        return "owner"
    if await user_repo.is_sudo(user_id):
        return "sudo"
    if chat_id:
        if await admin_repo.is_player_owner(user_id, chat_id):
            return "player_owner"
        if await admin_repo.is_player_deputy(user_id, chat_id):
            return "player_deputy"
        if await admin_repo.is_music_admin(user_id, chat_id):
            return "music_admin"
        if await admin_repo.is_video_admin(user_id, chat_id):
            return "video_admin"
        if await admin_repo.is_vip(user_id, chat_id):
            return "vip"
    return "regular"


def _query_chat(query: CallbackQuery):
    return query.message.chat if query.message else None


def _chat_type_value(chat) -> str | None:
    chat_type_raw = getattr(chat, "type", None) if chat else None
    value = getattr(chat_type_raw, "value", chat_type_raw)
    return str(value) if value is not None else None


def _is_group_chat(chat) -> bool:
    return _chat_type_value(chat) in ("group", "supergroup")


def _is_group_query(query: CallbackQuery) -> bool:
    return _is_group_chat(_query_chat(query))


async def _deny_help_outside_group(query: CallbackQuery) -> None:
    await _answer_callback(
        query,
        t(_LANG, "help.errors.group_only_callback"),
        show_alert=True,
    )


async def render_help_home_panel(client: Client, query: CallbackQuery) -> bool:
    """Render Help home with callbacks bound to the opener."""
    if not _is_group_query(query):
        await _deny_help_outside_group(query)
        return False

    user_id = query.from_user.id if query.from_user else 0
    kb = KeyboardFactory.help_home(_LANG, "regular", is_group=_is_group_query(query), user_id=user_id)
    return await panel_callback_edit(client, query, t(_LANG, "help.title"), kb)


async def _render_reference_page(
    client: Client,
    query: CallbackQuery,
    page: str,
    opener_user_id: int,
) -> None:
    if page == CB["HELP_HOME"]:
        await _safe_help_edit(
            client,
            query,
            t(_LANG, "help.title"),
            reply_markup=KeyboardFactory.help_home(
                _LANG,
                "regular",
                is_group=_is_group_query(query),
                user_id=opener_user_id,
            ),
        )
        return

    if page == CB["HELP_PLAYBACK"]:
        await _safe_help_edit(
            client,
            query,
            t(_LANG, "help.play_menu"),
            reply_markup=KeyboardFactory.help_play_menu(_LANG, opener_user_id),
        )
        return

    if page == CB["HELP_PUBLIC"]:
        await _safe_help_edit(
            client,
            query,
            t(_LANG, "help.public_menu"),
            reply_markup=KeyboardFactory.help_public_menu(_LANG, opener_user_id),
        )
        return

    if page == CB["HELP_PROMOTE"]:
        await _safe_help_edit(
            client,
            query,
            t(_LANG, "help.promote_menu"),
            reply_markup=KeyboardFactory.help_promote_menu(_LANG, opener_user_id),
        )
        return

    detail = _DETAIL_PAGES.get(page)
    if detail is not None:
        page_key, parent_cb = detail
        await _safe_help_edit(
            client,
            query,
            t(_LANG, f"help.pages.{page_key}"),
            reply_markup=KeyboardFactory.help_detail_nav(_LANG, parent_cb, opener_user_id),
        )
        return

    legacy_key = _LEGACY_PUBLIC_SECTIONS.get(page)
    if legacy_key is not None:
        await _safe_help_edit(
            client,
            query,
            t(_LANG, f"help.{legacy_key}"),
            reply_markup=KeyboardFactory.help_detail_nav(_LANG, CB["HELP_HOME"], opener_user_id),
        )
        return

    await _answer_callback(
        query,
        t(_LANG, "common.errors.unknown_callback"),
        show_alert=True,
    )


async def _render_admin_legacy_page(
    client: Client,
    query: CallbackQuery,
    page: str,
    opener_user_id: int,
) -> bool:
    entry = _ADMIN_SECTIONS.get(page)
    if entry is None:
        return False

    section_key, allowed_roles = entry
    chat = _query_chat(query)
    chat_id = chat.id if chat else None
    role = await _get_role(opener_user_id, chat_id if _is_group_query(query) else None)
    if role not in allowed_roles:
        await _safe_help_edit(
            client,
            query,
            t(_LANG, "help.access_denied"),
            reply_markup=KeyboardFactory.help_detail_nav(_LANG, CB["HELP_HOME"], opener_user_id),
        )
        return True

    if (
        section_key == "sudo_panel"
        and not is_developer(opener_user_id)
        and await deny_if_sudo_panel_disabled(query, opener_user_id)
    ):
        return True

    await _safe_help_edit(
        client,
        query,
        t(_LANG, f"help.{section_key}"),
        reply_markup=KeyboardFactory.help_detail_nav(_LANG, CB["HELP_HOME"], opener_user_id),
    )
    return True


async def handle_help_callback(client: Client, query: CallbackQuery) -> None:
    """Route h:* callbacks after validating the optional user binding."""
    parsed = parse_help_callback(query.data)
    if parsed is None:
        await _answer_callback(
            query,
            t(_LANG, "help.errors.invalid_callback"),
            show_alert=True,
        )
        return

    current_user_id = query.from_user.id if query.from_user else None
    if current_user_id is None:
        await _answer_callback(
            query,
            t(_LANG, "help.errors.invalid_callback"),
            show_alert=True,
        )
        return

    if parsed.bound_user_id is not None and parsed.bound_user_id != current_user_id:
        await _answer_callback(
            query,
            t(_LANG, "help.errors.not_for_you"),
            show_alert=True,
        )
        return

    opener_user_id = parsed.bound_user_id or current_user_id

    chat_type, user_id, message_chat_id, data = _callback_context(query)
    logger.info(
        "callback received data=%s chat_type=%s user_id=%s message_chat_id=%s "
        "selected_handler=help",
        data,
        chat_type,
        user_id,
        message_chat_id,
    )

    if not _is_group_query(query):
        await _deny_help_outside_group(query)
        return

    if parsed.page == CB["HELP_CLOSE"]:
        await _safe_help_close(query)
        return

    if await _render_admin_legacy_page(client, query, parsed.page, opener_user_id):
        return

    await _render_reference_page(client, query, parsed.page, opener_user_id)


def _find_help_callback_handlers(bot: Client) -> list[tuple[int, str, object]]:
    """Return all registered Help route handlers as ``(group, name, handler)``."""
    from pyrogram.handlers import CallbackQueryHandler

    found: list[tuple[int, str, object]] = []
    for group, handlers in bot.dispatcher.groups.items():
        for handler in handlers:
            if not isinstance(handler, CallbackQueryHandler):
                continue
            orig = getattr(handler, "original_callback", handler.callback)
            name = getattr(orig, "__name__", "")
            if name.startswith("help_route_"):
                found.append((group, name, handler))
    return found


def _find_help_callback_handler(bot: Client):
    """Return the first registered Help route handler, if any."""
    handlers = _find_help_callback_handlers(bot)
    if not handlers:
        return None, None
    group, _name, handler = handlers[0]
    return handler, group


def _help_router_registered(bot: Client) -> bool:
    """True when at least one Help route handler is present in the dispatcher."""
    return bool(_find_help_callback_handlers(bot))


async def verify_help_callback_router(bot: Client, *, sample_user_id: int = 1) -> bool:
    """Verify Help routes via Kurigram dispatcher probes (delegates to help_diag)."""
    from app.handlers.help_diag import verify_help_routing

    return await verify_help_routing(bot)


def _register_help_callback_routes(bot: Client) -> None:
    """Register explicit Help regex handlers at ``HELP_CALLBACK_GROUP``."""

    def _register_route(route_name: str, pattern: str) -> None:
        @bot.on_callback_query(filters.regex(pattern), group=HELP_CALLBACK_GROUP)
        async def help_route(client: Client, query: CallbackQuery) -> None:
            await handle_help_callback(client, query)

        help_route.__name__ = route_name
        help_route.__qualname__ = route_name

    for route_name, pattern in HELP_ROUTE_SPECS:
        _register_route(route_name, pattern)


def register(bot: Client, call_py) -> None:  # noqa: ARG001
    from app.handlers.help_diag import log_help_center_register_called, log_help_handlers_added

    if getattr(bot, "_help_center_registered", False) and _help_router_registered(bot):
        return

    log_help_center_register_called()

    @bot.on_message(help_command_filter(), group=PRIORITY_COMMAND_GROUP)
    async def help_command(client: Client, message: Message):  # noqa: ARG001
        if not _is_group_chat(message.chat):
            await message.reply(t(_LANG, "help.group_only"))
            return
        user_id = message.from_user.id if message.from_user else 0
        kb = KeyboardFactory.help_home(_LANG, "regular", is_group=True, user_id=user_id)
        await message.reply(t(_LANG, "help.title"), reply_markup=kb)

    _register_help_callback_routes(bot)

    bot._help_center_registered = True
    log_help_handlers_added(len(HELP_ROUTE_SPECS))
