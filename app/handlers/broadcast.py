from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import Message
from pyromod.exceptions import ListenerStopped

from app.services import BroadcastService
from app.utils.ask_result import (
    deliver_ask_outcome,
    prompt_for_panel_input,
    safe_delete_user_input,
    safe_stop_listening,
)
from app.utils.decorators import developer_only
from app.utils.filters import dev_filter
from app.utils.i18n import t
from app.services.wizard_ui import (
    TOKEN_DEV_BROADCAST,
    TOKEN_ROLE_ROOT,
    build_cancel_kb,
    build_done_kb,
    remember_return_token,
)

logger = logging.getLogger(__name__)

_LANG = "fa"
_TIMEOUT = 60


async def _ask(
    client: Client,
    chat_id: int,
    key: str,
    *,
    user_id: int | None = None,
    return_to: str = TOKEN_ROLE_ROOT,
) -> Message | None:
    if user_id is not None:
        await remember_return_token(user_id, return_to)
    stopped = await safe_stop_listening(client, chat_id, user_id=user_id)
    logger.debug(
        "legacy broadcast ask start key=%s chat_id=%s user_id=%s stop_listening=%s",
        key,
        chat_id,
        user_id,
        stopped,
    )
    try:
        resp = await prompt_for_panel_input(
            client,
            chat_id,
            user_id,
            t(_LANG, key),
            build_cancel_kb(_LANG, return_to),
            timeout=_TIMEOUT,
        )
        if resp and resp.text and resp.text.strip().lower() in ("/cancel", "cancel", "لغو"):
            await safe_delete_user_input(resp)
            await deliver_ask_outcome(
                client,
                chat_id,
                user_id if user_id is not None else chat_id,
                t(_LANG, "common.cancelled"),
                build_done_kb(_LANG, return_to),
            )
            return None
        return resp
    except ListenerStopped:
        logger.debug("legacy broadcast ask listener stopped key=%s chat_id=%s user_id=%s", key, chat_id, user_id)
        return None
    except Exception as exc:
        logger.debug(
            "legacy broadcast ask failed key=%s chat_id=%s user_id=%s reason=%s",
            key,
            chat_id,
            user_id,
            type(exc).__name__,
        )
        await deliver_ask_outcome(
            client,
            chat_id,
            user_id if user_id is not None else chat_id,
            t(_LANG, "ask.timeout"),
            build_done_kb(_LANG, return_to),
        )
        return None


async def handle_broadcast(
    client: Client,
    chat_id: int,
    target: str,
    mode: str,
    *,
    user_id: int | None = None,
) -> None:
    """Shared broadcast logic used by dev/owner panels and direct commands."""
    resp = await _ask(client, chat_id, "ask.broadcast_msg", user_id=user_id, return_to=TOKEN_DEV_BROADCAST)
    if resp is None:
        return
    anchor_user_id = user_id if user_id is not None else chat_id
    await deliver_ask_outcome(
        client,
        chat_id,
        anchor_user_id,
        t(_LANG, "broadcast.started"),
        build_done_kb(_LANG, TOKEN_DEV_BROADCAST),
    )

    if target == "group":
        stats = await BroadcastService.broadcast_to_groups(client, resp, mode)
    elif target == "channel":
        stats = await BroadcastService.broadcast_to_channels(client, resp, mode)
    else:
        stats = await BroadcastService.broadcast_to_users(client, resp, mode)

    total = stats["sent"] + stats["failed"]
    await deliver_ask_outcome(
        client,
        chat_id,
        anchor_user_id,
        t(_LANG, "broadcast.stats", sent=stats["sent"], failed=stats["failed"], total=total),
        build_done_kb(_LANG, TOKEN_DEV_BROADCAST),
    )


def register(bot: Client, call_py) -> None:  # noqa: ARG001

    @bot.on_message(filters.command("broadcast_groups") & filters.private & dev_filter())
    @developer_only
    async def cmd_bc_groups(client: Client, message: Message):
        await handle_broadcast(client, message.chat.id, "group", "copy", user_id=message.from_user.id if message.from_user else None)

    @bot.on_message(filters.command("forward_groups") & filters.private & dev_filter())
    @developer_only
    async def cmd_fw_groups(client: Client, message: Message):
        await handle_broadcast(client, message.chat.id, "group", "forward", user_id=message.from_user.id if message.from_user else None)

    @bot.on_message(filters.command("broadcast_channels") & filters.private & dev_filter())
    @developer_only
    async def cmd_bc_channels(client: Client, message: Message):
        await handle_broadcast(client, message.chat.id, "channel", "copy", user_id=message.from_user.id if message.from_user else None)

    @bot.on_message(filters.command("broadcast_users") & filters.private & dev_filter())
    @developer_only
    async def cmd_bc_users(client: Client, message: Message):
        await handle_broadcast(client, message.chat.id, "private", "copy", user_id=message.from_user.id if message.from_user else None)

    @bot.on_message(filters.command("forward_users") & filters.private & dev_filter())
    @developer_only
    async def cmd_fw_users(client: Client, message: Message):
        await handle_broadcast(client, message.chat.id, "private", "forward", user_id=message.from_user.id if message.from_user else None)






