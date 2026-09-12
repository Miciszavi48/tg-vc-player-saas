"""Raw MTProto group-call participant moderation for Call Security."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.database.engine import async_session
from app.database.models import HelperAccount, HelperChatBinding
from app.services.helper_pool_service import HelperPoolService
from app.utils.helper_admin_rights import helper_has_call_admin_rights

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GroupCallModerationResult:
    ok: bool
    supported: bool
    reason: str
    error_class: str | None = None
    helper_id: int | None = None
    helper_user_id: int | None = None
    binding_source: str | None = None


@dataclass(frozen=True)
class GroupCallMuteReadiness:
    raw_mute_api_available: bool
    active_call_known: bool
    helper_available: bool
    reason: str


@dataclass(frozen=True)
class HelperResolutionResult:
    status: str
    chat_id: int
    helper_id: int | None = None
    helper_user_id: int | None = None
    client: Any | None = None
    client_context: Any | None = None
    binding_source: str | None = None
    membership_status: str | None = None
    admin_status: str | None = None
    message_key: str | None = None
    error_class: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


_RESOLUTION_REASON_MAP = {
    "no_helper": "no_helper_available",
    "session_unavailable": "helper_session_unavailable",
    "join_failed": "helper_join_failed",
    "not_in_group": "helper_not_in_group",
    "not_admin": "helper_not_admin",
    "api_failed": "api_error",
}

_HELPER_ADMIN_FLAGS = (
    "can_manage_video_chats",
    "can_manage_voice_chats",
    "can_manage_chat",
)


def _raw_namespace() -> Any | None:
    try:
        from pyrogram import raw

        return raw
    except Exception:
        return None


def raw_mute_api_available() -> bool:
    """Return whether Kurigram/Pyrogram exposes the raw mute method locally."""
    return _raw_phone_function_available("EditGroupCallParticipant")


def _raw_phone_function_available(name: str) -> bool:
    """Return whether the local Kurigram/Pyrogram raw phone function exists."""
    raw = _raw_namespace()
    if raw is None:
        return False
    return callable(getattr(getattr(raw.functions, "phone", None), name, None))


def raw_create_group_call_available() -> bool:
    return _raw_phone_function_available("CreateGroupCall")


def raw_discard_group_call_available() -> bool:
    return _raw_phone_function_available("DiscardGroupCall")


def raw_invite_group_call_available() -> bool:
    return _raw_phone_function_available("InviteToGroupCall")


def raw_edit_group_call_title_available() -> bool:
    return _raw_phone_function_available("EditGroupCallTitle")


def raw_export_group_call_invite_available() -> bool:
    return _raw_phone_function_available("ExportGroupCallInvite")


def raw_toggle_group_call_settings_available() -> bool:
    return _raw_phone_function_available("ToggleGroupCallSettings")


async def probe_group_call_mute_support(client: Any | None = None) -> bool:
    """Probe raw API support without calling Telegram."""
    if not raw_mute_api_available():
        return False
    if client is None:
        return True
    return callable(getattr(client, "invoke", None)) and callable(
        getattr(client, "resolve_peer", None)
    )


def _result(
    ok: bool,
    supported: bool,
    reason: str,
    exc: Exception | None = None,
    *,
    helper_id: int | None = None,
    helper_user_id: int | None = None,
    binding_source: str | None = None,
) -> GroupCallModerationResult:
    return GroupCallModerationResult(
        ok=ok,
        supported=supported,
        reason=reason,
        error_class=type(exc).__name__ if exc is not None else None,
        helper_id=helper_id,
        helper_user_id=helper_user_id,
        binding_source=binding_source,
    )


def _result_from_resolution(resolution: HelperResolutionResult) -> GroupCallModerationResult:
    return GroupCallModerationResult(
        ok=False,
        supported=True,
        reason=_RESOLUTION_REASON_MAP.get(resolution.status, "api_error"),
        error_class=resolution.error_class,
        helper_id=resolution.helper_id,
        helper_user_id=resolution.helper_user_id,
        binding_source=resolution.binding_source,
    )


def _helper_runtime_eligible(helper: HelperAccount, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    def _future(dt: datetime | None) -> bool:
        if dt is None:
            return False
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt > now

    if helper.status != "active":
        return False
    if helper.current_active_calls >= helper.max_concurrent_calls:
        return False
    if _future(helper.cooldown_until):
        return False
    if _future(helper.banned_until):
        return False
    return True


async def _bound_helper_with_source(chat_id: int) -> tuple[HelperAccount | None, str | None]:
    async with async_session() as session:
        stmt = (
            select(HelperAccount)
            .join(HelperChatBinding, HelperChatBinding.helper_account_id == HelperAccount.id)
            .where(HelperChatBinding.chat_id == chat_id)
            .limit(1)
        )
        helper = (await session.execute(stmt)).scalar_one_or_none()
    if helper is None:
        return None, None
    if not _helper_runtime_eligible(helper):
        return None, "bound_unavailable"
    return helper, "bound"


async def _pool_helper_excluding(excluded_ids: set[int]) -> HelperAccount | None:
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        stmt = (
            select(HelperAccount)
            .where(
                HelperAccount.status == "active",
                HelperAccount.current_active_calls < HelperAccount.max_concurrent_calls,
                (HelperAccount.cooldown_until.is_(None)) | (HelperAccount.cooldown_until <= now),
                (HelperAccount.banned_until.is_(None)) | (HelperAccount.banned_until <= now),
            )
            .order_by(HelperAccount.current_active_calls.asc(), HelperAccount.id.asc())
        )
        if excluded_ids:
            stmt = stmt.where(HelperAccount.id.not_in(excluded_ids))
        result = await session.execute(stmt.limit(1))
        return result.scalar_one_or_none()


async def _helper_candidates_for_chat(chat_id: int) -> list[tuple[HelperAccount, str]]:
    candidates: list[tuple[HelperAccount, str]] = []
    excluded: set[int] = set()
    bound, bound_source = await _bound_helper_with_source(chat_id)
    if bound is not None:
        candidates.append((bound, "bound"))
        excluded.add(int(bound.id))
    pool = await _pool_helper_excluding(excluded)
    if pool is not None:
        candidates.append((pool, "pool" if bound_source is None else "pool_fallback"))
    return candidates


async def _maybe_await(value: Any) -> Any:
    import inspect

    if inspect.isawaitable(value):
        return await value
    return value


async def _start_client(client: Any) -> tuple[Any, Any, str]:
    start = getattr(client, "start", None)
    if callable(start):
        active = await _maybe_await(start())
        return active or client, client, "start"
    enter = getattr(client, "__aenter__", None)
    if callable(enter):
        active = await enter()
        return active or client, client, "context"
    return client, client, "none"


async def close_group_call_helper(resolution: HelperResolutionResult) -> None:
    context = resolution.client_context or resolution.client
    if context is None:
        return
    stop = getattr(context, "stop", None)
    if callable(stop):
        try:
            await _maybe_await(stop())
        except Exception:
            logger.debug(
                "helper resolver client stop failed chat_id=%s helper_id=%s",
                resolution.chat_id,
                resolution.helper_id,
            )
        return
    exit_method = getattr(context, "__aexit__", None)
    if callable(exit_method):
        try:
            await exit_method(None, None, None)
        except Exception:
            logger.debug(
                "helper resolver client exit failed chat_id=%s helper_id=%s",
                resolution.chat_id,
                resolution.helper_id,
            )


def _member_status(member: Any) -> str:
    status = getattr(getattr(member, "status", None), "value", None) or getattr(
        member,
        "status",
        None,
    )
    return str(status or "").lower()


def _member_user_id(member: Any, fallback: int | None) -> int | None:
    user = getattr(member, "user", None)
    user_id = getattr(user, "id", None)
    return int(user_id) if user_id is not None else fallback


def _member_can_manage_calls(member: Any) -> bool:
    return helper_has_call_admin_rights(member)


async def resolve_group_call_helper(
    chat_id: int,
    *,
    require_admin: bool,
    reason: str,
) -> HelperResolutionResult:
    """Resolve a same-group helper user client for raw group-call actions."""
    candidates = await _helper_candidates_for_chat(chat_id)
    if not candidates:
        logger.debug(
            "group_call_helper resolve failed chat_id=%s reason=%s status=no_helper",
            chat_id,
            reason,
        )
        return HelperResolutionResult(
            status="no_helper",
            chat_id=chat_id,
            message_key="group_text_call.no_helper",
        )

    last_failure: HelperResolutionResult | None = None
    for helper, binding_source in candidates:
        helper_id = int(helper.id)
        if not helper.session_string_enc:
            last_failure = HelperResolutionResult(
                status="session_unavailable",
                chat_id=chat_id,
                helper_id=helper_id,
                helper_user_id=helper.tg_user_id,
                binding_source=binding_source,
                message_key="group_text_call.helper_session_unavailable",
            )
            logger.debug(
                "group_call_helper resolve failed chat_id=%s helper_id=%s helper_user_id=%s source=%s status=session_unavailable reason=%s",
                chat_id,
                helper_id,
                helper.tg_user_id,
                binding_source,
                reason,
            )
            continue

        session_string = await HelperPoolService.get_helper_session(helper_id)
        if session_string is None:
            last_failure = HelperResolutionResult(
                status="session_unavailable",
                chat_id=chat_id,
                helper_id=helper_id,
                helper_user_id=helper.tg_user_id,
                binding_source=binding_source,
                message_key="group_text_call.helper_session_unavailable",
            )
            logger.debug(
                "group_call_helper resolve failed chat_id=%s helper_id=%s helper_user_id=%s source=%s status=session_unavailable reason=%s",
                chat_id,
                helper_id,
                helper.tg_user_id,
                binding_source,
                reason,
            )
            continue

        from app.services.bot_update_service import get_runtime_bot

        bot_client = get_runtime_bot()
        if bot_client is None:
            joined = await HelperPoolService.ensure_helper_joined(helper_id, chat_id)
        else:
            joined = await HelperPoolService.ensure_helper_joined(
                helper_id,
                chat_id,
                bot_client=bot_client,
            )
        if not joined:
            last_failure = HelperResolutionResult(
                status="join_failed",
                chat_id=chat_id,
                helper_id=helper_id,
                helper_user_id=helper.tg_user_id,
                binding_source=binding_source,
                message_key="group_text_call.helper_join_failed",
            )
            logger.debug(
                "group_call_helper resolve failed chat_id=%s helper_id=%s helper_user_id=%s source=%s status=join_failed reason=%s",
                chat_id,
                helper_id,
                helper.tg_user_id,
                binding_source,
                reason,
            )
            continue

        client_context = HelperPoolService.build_client(
            f"group_call_helper_{reason}_{helper_id}",
            session_string,
            helper,
        )
        client = None
        context = client_context
        try:
            client, context, _mode = await _start_client(client_context)
            get_member = getattr(client, "get_chat_member", None)
            if not callable(get_member):
                await close_group_call_helper(
                    HelperResolutionResult(
                        status="api_failed",
                        chat_id=chat_id,
                        helper_id=helper_id,
                        helper_user_id=helper.tg_user_id,
                        client=client,
                        client_context=context,
                        binding_source=binding_source,
                    )
                )
                return HelperResolutionResult(
                    status="api_failed",
                    chat_id=chat_id,
                    helper_id=helper_id,
                    helper_user_id=helper.tg_user_id,
                    binding_source=binding_source,
                    message_key="group_text_call.api_error",
                )
            member = await get_member(chat_id, "me")
            membership_status = _member_status(member)
            helper_user_id = _member_user_id(member, helper.tg_user_id)
            if membership_status in {"left", "kicked", "banned"}:
                await close_group_call_helper(
                    HelperResolutionResult(
                        status="not_in_group",
                        chat_id=chat_id,
                        helper_id=helper_id,
                        helper_user_id=helper_user_id,
                        client=client,
                        client_context=context,
                        binding_source=binding_source,
                    )
                )
                last_failure = HelperResolutionResult(
                    status="not_in_group",
                    chat_id=chat_id,
                    helper_id=helper_id,
                    helper_user_id=helper_user_id,
                    binding_source=binding_source,
                    membership_status=membership_status,
                    message_key="group_text_call.helper_not_in_group",
                )
                logger.debug(
                    "group_call_helper resolve failed chat_id=%s helper_id=%s helper_user_id=%s source=%s membership=%s status=not_in_group reason=%s",
                    chat_id,
                    helper_id,
                    helper_user_id,
                    binding_source,
                    membership_status,
                    reason,
                )
                continue
            admin_ok = _member_can_manage_calls(member)
            admin_status = "admin" if admin_ok else "member"
            if require_admin and not admin_ok:
                await close_group_call_helper(
                    HelperResolutionResult(
                        status="not_admin",
                        chat_id=chat_id,
                        helper_id=helper_id,
                        helper_user_id=helper_user_id,
                        client=client,
                        client_context=context,
                        binding_source=binding_source,
                    )
                )
                logger.debug(
                    "group_call_helper resolve failed chat_id=%s helper_id=%s helper_user_id=%s source=%s membership=%s admin=%s status=not_admin reason=%s",
                    chat_id,
                    helper_id,
                    helper_user_id,
                    binding_source,
                    membership_status,
                    admin_status,
                    reason,
                )
                return HelperResolutionResult(
                    status="not_admin",
                    chat_id=chat_id,
                    helper_id=helper_id,
                    helper_user_id=helper_user_id,
                    binding_source=binding_source,
                    membership_status=membership_status,
                    admin_status=admin_status,
                    message_key="group_text_call.helper_not_admin",
                )

            await HelperPoolService.bind_chat_to_helper(chat_id, helper_id)
            logger.debug(
                "group_call_helper resolved chat_id=%s helper_id=%s helper_user_id=%s source=%s membership=%s admin=%s reason=%s",
                chat_id,
                helper_id,
                helper_user_id,
                binding_source,
                membership_status,
                admin_status,
                reason,
            )
            return HelperResolutionResult(
                status="ok",
                chat_id=chat_id,
                helper_id=helper_id,
                helper_user_id=helper_user_id,
                client=client,
                client_context=context,
                binding_source=binding_source,
                membership_status=membership_status,
                admin_status=admin_status,
            )
        except Exception as exc:
            await close_group_call_helper(
                HelperResolutionResult(
                    status="api_failed",
                    chat_id=chat_id,
                    helper_id=helper_id,
                    helper_user_id=helper.tg_user_id,
                    client=client,
                    client_context=context,
                    binding_source=binding_source,
                    error_class=type(exc).__name__,
                )
            )
            mapped = _map_error(exc)
            status = "not_admin" if mapped == "permission_denied" else "api_failed"
            if status == "not_admin":
                return HelperResolutionResult(
                    status="not_admin",
                    chat_id=chat_id,
                    helper_id=helper_id,
                    helper_user_id=helper.tg_user_id,
                    binding_source=binding_source,
                    message_key="group_text_call.helper_not_admin",
                    error_class=type(exc).__name__,
                )
            last_failure = HelperResolutionResult(
                status="api_failed",
                chat_id=chat_id,
                helper_id=helper_id,
                helper_user_id=helper.tg_user_id,
                binding_source=binding_source,
                message_key="group_text_call.api_error",
                error_class=type(exc).__name__,
            )
            logger.debug(
                "group_call_helper resolve failed chat_id=%s helper_id=%s helper_user_id=%s source=%s status=api_failed err=%s reason=%s",
                chat_id,
                helper_id,
                helper.tg_user_id,
                binding_source,
                type(exc).__name__,
                reason,
            )
            continue

    return last_failure or HelperResolutionResult(
        status="no_helper",
        chat_id=chat_id,
        message_key="group_text_call.no_helper",
    )


def _map_error(exc: Exception) -> str:
    name = type(exc).__name__
    upper = f"{name} {str(exc)}".upper()
    if "FLOOD_WAIT" in upper or name == "FloodWait":
        return "flood_wait"
    if "GROUPCALL_ALREADY_STARTED" in upper:
        return "already_active"
    if "PUBLIC_CHANNEL_MISSING" in upper:
        return "link_unavailable"
    if any(
        token in upper
        for token in (
            "CHAT_ADMIN_REQUIRED",
            "GROUPCALL_FORBIDDEN",
            "CHANNEL_PRIVATE",
            "FORBIDDEN",
        )
    ):
        return "permission_denied"
    if any(
        token in upper
        for token in (
            "PARTICIPANT_JOIN_MISSING",
            "PEER_ID_INVALID",
            "USER_NOT_PARTICIPANT",
        )
    ):
        return "participant_not_found"
    if any(token in upper for token in ("GROUPCALL_INVALID", "GROUP_CALL_INVALID")):
        return "no_active_call"
    return "api_error"


def _client_supports_raw_phone(client: Any) -> bool:
    return callable(getattr(client, "invoke", None)) and callable(
        getattr(client, "resolve_peer", None)
    )


def _input_channel_from_peer(raw: Any, peer: Any) -> Any | None:
    cls_name = type(peer).__name__
    if cls_name == "InputChannel":
        return peer
    if cls_name == "InputPeerChannel":
        channel_id = getattr(peer, "channel_id", None)
        access_hash = getattr(peer, "access_hash", None)
        if channel_id is None or access_hash is None:
            return None
        return raw.types.InputChannel(channel_id=channel_id, access_hash=access_hash)
    return None


def _input_user_from_peer(raw: Any, peer: Any) -> Any | None:
    cls_name = type(peer).__name__
    if cls_name == "InputUser":
        return peer
    if cls_name == "InputUserSelf":
        return peer
    if cls_name == "InputPeerSelf":
        return raw.types.InputUserSelf()
    if cls_name == "InputPeerUser":
        user_id = getattr(peer, "user_id", None)
        access_hash = getattr(peer, "access_hash", None)
        if user_id is None or access_hash is None:
            return None
        return raw.types.InputUser(user_id=user_id, access_hash=access_hash)
    return None


async def resolve_active_input_group_call(client: Any, chat_id: int) -> Any | None:
    """Resolve the active raw InputGroupCall for a group/supergroup/channel."""
    raw = _raw_namespace()
    if raw is None:
        return None
    peer = await client.resolve_peer(chat_id)
    cls_name = type(peer).__name__
    if cls_name in {"InputPeerChannel", "InputChannel"}:
        channel = _input_channel_from_peer(raw, peer)
        if channel is None:
            return None
        result = await client.invoke(raw.functions.channels.GetFullChannel(channel=channel))
    elif cls_name == "InputPeerChat":
        result = await client.invoke(
            raw.functions.messages.GetFullChat(chat_id=int(peer.chat_id))
        )
    else:
        return None
    full_chat = getattr(result, "full_chat", None)
    return getattr(full_chat, "call", None)


async def mute_group_call_participant(
    client: Any,
    chat_id: int,
    user_id: int,
    *,
    muted: bool,
) -> GroupCallModerationResult:
    """Invoke raw phone.EditGroupCallParticipant for a specific participant."""
    raw = _raw_namespace()
    if raw is None or not await probe_group_call_mute_support(client):
        return _result(False, False, "raw_api_unavailable")

    try:
        input_group_call = await resolve_active_input_group_call(client, chat_id)
        if input_group_call is None:
            return _result(False, True, "no_active_call")
        participant = await client.resolve_peer(user_id)
        await client.invoke(
            raw.functions.phone.EditGroupCallParticipant(
                call=input_group_call,
                participant=participant,
                muted=bool(muted),
            )
        )
        return _result(True, True, "muted" if muted else "unmuted")
    except Exception as exc:
        reason = _map_error(exc)
        logger.info(
            "group_call_moderation failed chat_id=%s user_id=%s muted=%s reason=%s err=%s",
            chat_id,
            user_id,
            bool(muted),
            reason,
            type(exc).__name__,
        )
        return _result(False, True, reason, exc)


async def create_group_call(
    client: Any,
    chat_id: int,
    *,
    title: str | None = None,
) -> GroupCallModerationResult:
    """Invoke raw phone.CreateGroupCall for a group/channel."""
    raw = _raw_namespace()
    if raw is None or not raw_create_group_call_available() or not _client_supports_raw_phone(client):
        return _result(False, False, "raw_api_unavailable")
    try:
        peer = await client.resolve_peer(chat_id)
        await client.invoke(
            raw.functions.phone.CreateGroupCall(
                peer=peer,
                random_id=random.randint(1, 0x7FFFFFFF),
                title=title.strip() if title and title.strip() else None,
            )
        )
        return _result(True, True, "started")
    except Exception as exc:
        reason = _map_error(exc)
        logger.info(
            "group_call_create failed chat_id=%s reason=%s err=%s",
            chat_id,
            reason,
            type(exc).__name__,
        )
        return _result(False, True, reason, exc)


async def discard_group_call(client: Any, chat_id: int) -> GroupCallModerationResult:
    """Invoke raw phone.DiscardGroupCall for the active group call."""
    raw = _raw_namespace()
    if raw is None or not raw_discard_group_call_available() or not _client_supports_raw_phone(client):
        return _result(False, False, "raw_api_unavailable")
    try:
        input_group_call = await resolve_active_input_group_call(client, chat_id)
        if input_group_call is None:
            return _result(False, True, "no_active_call")
        await client.invoke(raw.functions.phone.DiscardGroupCall(call=input_group_call))
        return _result(True, True, "discarded")
    except Exception as exc:
        reason = _map_error(exc)
        logger.info(
            "group_call_discard failed chat_id=%s reason=%s err=%s",
            chat_id,
            reason,
            type(exc).__name__,
        )
        return _result(False, True, reason, exc)


async def invite_to_group_call(
    client: Any,
    chat_id: int,
    user_ids: list[int],
) -> GroupCallModerationResult:
    """Invoke raw phone.InviteToGroupCall for resolved InputUsers."""
    raw = _raw_namespace()
    if raw is None or not raw_invite_group_call_available() or not _client_supports_raw_phone(client):
        return _result(False, False, "raw_api_unavailable")
    try:
        input_group_call = await resolve_active_input_group_call(client, chat_id)
        if input_group_call is None:
            return _result(False, True, "no_active_call")
        input_users: list[Any] = []
        for user_id in dict.fromkeys(int(uid) for uid in user_ids):
            peer = await client.resolve_peer(user_id)
            input_user = _input_user_from_peer(raw, peer)
            if input_user is None:
                return _result(False, True, "participant_not_found")
            input_users.append(input_user)
        if not input_users:
            return _result(False, True, "no_users")
        await client.invoke(
            raw.functions.phone.InviteToGroupCall(
                call=input_group_call,
                users=input_users,
            )
        )
        return _result(True, True, "invited")
    except Exception as exc:
        reason = _map_error(exc)
        logger.info(
            "group_call_invite failed chat_id=%s users=%s reason=%s err=%s",
            chat_id,
            len(user_ids),
            reason,
            type(exc).__name__,
        )
        return _result(False, True, reason, exc)


async def edit_group_call_title(
    client: Any,
    chat_id: int,
    title: str,
) -> GroupCallModerationResult:
    """Invoke raw phone.EditGroupCallTitle for the active group call."""
    raw = _raw_namespace()
    if raw is None or not raw_edit_group_call_title_available() or not _client_supports_raw_phone(client):
        return _result(False, False, "raw_api_unavailable")
    try:
        input_group_call = await resolve_active_input_group_call(client, chat_id)
        if input_group_call is None:
            return _result(False, True, "no_active_call")
        await client.invoke(
            raw.functions.phone.EditGroupCallTitle(
                call=input_group_call,
                title=title,
            )
        )
        return _result(True, True, "title_applied")
    except Exception as exc:
        reason = _map_error(exc)
        logger.info(
            "group_call_title failed chat_id=%s reason=%s err=%s",
            chat_id,
            reason,
            type(exc).__name__,
        )
        return _result(False, True, reason, exc)


async def export_group_call_invite_link(
    client: Any,
    chat_id: int,
) -> tuple[GroupCallModerationResult, str | None]:
    """Invoke raw phone.ExportGroupCallInvite and return Telegram's link."""
    raw = _raw_namespace()
    if raw is None or not raw_export_group_call_invite_available() or not _client_supports_raw_phone(client):
        return _result(False, False, "raw_api_unavailable"), None
    try:
        input_group_call = await resolve_active_input_group_call(client, chat_id)
        if input_group_call is None:
            return _result(False, True, "no_active_call"), None
        exported = await client.invoke(
            raw.functions.phone.ExportGroupCallInvite(call=input_group_call)
        )
        link = getattr(exported, "link", None)
        if not link:
            return _result(False, True, "link_unavailable"), None
        return _result(True, True, "ok"), str(link)
    except Exception as exc:
        reason = _map_error(exc)
        logger.info(
            "group_call_invite_link failed chat_id=%s reason=%s err=%s",
            chat_id,
            reason,
            type(exc).__name__,
        )
        return _result(False, True, reason, exc), None


async def toggle_group_call_settings(
    client: Any,
    chat_id: int,
    *,
    join_muted: bool | None = None,
    messages_enabled: bool | None = None,
) -> GroupCallModerationResult:
    """Invoke raw phone.ToggleGroupCallSettings for live call settings."""
    raw = _raw_namespace()
    if raw is None or not raw_toggle_group_call_settings_available() or not _client_supports_raw_phone(client):
        return _result(False, False, "raw_api_unavailable")
    if join_muted is None and messages_enabled is None:
        return _result(True, True, "no_change")
    try:
        input_group_call = await resolve_active_input_group_call(client, chat_id)
        if input_group_call is None:
            return _result(False, True, "no_active_call")
        await client.invoke(
            raw.functions.phone.ToggleGroupCallSettings(
                call=input_group_call,
                join_muted=join_muted,
                messages_enabled=messages_enabled,
            )
        )
        return _result(True, True, "settings_applied")
    except Exception as exc:
        reason = _map_error(exc)
        logger.info(
            "group_call_settings failed chat_id=%s reason=%s err=%s",
            chat_id,
            reason,
            type(exc).__name__,
        )
        return _result(False, True, reason, exc)


async def _bound_helper(chat_id: int) -> HelperAccount | None:
    async with async_session() as session:
        stmt = (
            select(HelperAccount)
            .join(HelperChatBinding, HelperChatBinding.helper_account_id == HelperAccount.id)
            .where(
                HelperChatBinding.chat_id == chat_id,
                HelperAccount.status == "active",
            )
            .limit(1)
        )
        return (await session.execute(stmt)).scalar_one_or_none()


async def _select_helper_for_chat(chat_id: int) -> tuple[HelperAccount | None, str]:
    helper = await _bound_helper(chat_id)
    if helper is None:
        helper = await HelperPoolService.get_best_helper(chat_id)
    if helper is None:
        return None, "no_helper_available"
    if not helper.session_string_enc:
        return None, "no_helper_session"
    return helper, "ok"


async def has_eligible_helper_for_chat(chat_id: int) -> bool:
    helper, reason = await _select_helper_for_chat(chat_id)
    return helper is not None and reason == "ok"


async def get_call_mute_readiness(chat_id: int) -> GroupCallMuteReadiness:
    """Return panel-safe readiness status without contacting Telegram."""
    raw_ok = raw_mute_api_available()
    if not raw_ok:
        return GroupCallMuteReadiness(False, False, False, "raw_api_unavailable")
    try:
        from app.utils.cache import get_redis
        from app.utils.redis_keys import callsec_active_chat_key

        redis = await get_redis()
        active_call_known = await redis.get(callsec_active_chat_key(chat_id)) is not None
    except Exception:
        active_call_known = False
    if not active_call_known:
        return GroupCallMuteReadiness(True, False, False, "no_active_call")
    helper_available = await has_eligible_helper_for_chat(chat_id)
    if not helper_available:
        return GroupCallMuteReadiness(True, True, False, "no_helper_available")
    return GroupCallMuteReadiness(True, True, True, "ready")


async def try_mute_participant(
    chat_id: int,
    user_id: int,
    *,
    muted: bool = True,
    reason: str = "call_security",
) -> GroupCallModerationResult:
    """Select a helper and attempt raw participant mute/unmute."""
    if not raw_mute_api_available():
        return _result(False, False, "raw_api_unavailable")
    resolution = await resolve_group_call_helper(
        chat_id,
        require_admin=True,
        reason=f"{reason}_mute",
    )
    if not resolution.ok:
        return _result_from_resolution(resolution)
    try:
        result = await mute_group_call_participant(
            resolution.client,
            chat_id,
            user_id,
            muted=muted,
        )
        logger.info(
            "group_call_moderation result chat_id=%s helper_id=%s helper_user_id=%s user_id=%s muted=%s reason=%s ok=%s source=%s",
            chat_id,
            resolution.helper_id,
            resolution.helper_user_id,
            user_id,
            bool(muted),
            result.reason,
            result.ok,
            reason,
        )
        return GroupCallModerationResult(
            ok=result.ok,
            supported=result.supported,
            reason=result.reason,
            error_class=result.error_class,
            helper_id=resolution.helper_id,
            helper_user_id=resolution.helper_user_id,
            binding_source=resolution.binding_source,
        )
    except Exception as exc:
        mapped = _map_error(exc)
        logger.info(
            "group_call_moderation helper failed chat_id=%s helper_id=%s helper_user_id=%s user_id=%s muted=%s reason=%s err=%s",
            chat_id,
            resolution.helper_id,
            resolution.helper_user_id,
            user_id,
            bool(muted),
            mapped,
            type(exc).__name__,
        )
        return _result(
            False,
            True,
            mapped,
            exc,
            helper_id=resolution.helper_id,
            helper_user_id=resolution.helper_user_id,
            binding_source=resolution.binding_source,
        )
    finally:
        await close_group_call_helper(resolution)


async def try_unmute_participant(
    chat_id: int,
    user_id: int,
    *,
    reason: str = "call_security",
) -> GroupCallModerationResult:
    """Select a helper and attempt raw participant unmute."""
    return await try_mute_participant(
        chat_id,
        user_id,
        muted=False,
        reason=reason,
    )


async def _try_helper_group_call_operation(
    chat_id: int,
    operation,
    *,
    source: str,
    require_admin: bool = True,
) -> GroupCallModerationResult:
    resolution = await resolve_group_call_helper(
        chat_id,
        require_admin=require_admin,
        reason=source,
    )
    if not resolution.ok:
        return _result_from_resolution(resolution)
    try:
        result = await operation(resolution.client)
        logger.info(
            "group_call_action result chat_id=%s helper_id=%s helper_user_id=%s source=%s reason=%s ok=%s",
            chat_id,
            resolution.helper_id,
            resolution.helper_user_id,
            source,
            result.reason,
            result.ok,
        )
        return GroupCallModerationResult(
            ok=result.ok,
            supported=result.supported,
            reason=result.reason,
            error_class=result.error_class,
            helper_id=resolution.helper_id,
            helper_user_id=resolution.helper_user_id,
            binding_source=resolution.binding_source,
        )
    except Exception as exc:
        mapped = _map_error(exc)
        logger.info(
            "group_call_action helper failed chat_id=%s helper_id=%s helper_user_id=%s source=%s reason=%s err=%s",
            chat_id,
            resolution.helper_id,
            resolution.helper_user_id,
            source,
            mapped,
            type(exc).__name__,
        )
        return _result(
            False,
            True,
            mapped,
            exc,
            helper_id=resolution.helper_id,
            helper_user_id=resolution.helper_user_id,
            binding_source=resolution.binding_source,
        )
    finally:
        await close_group_call_helper(resolution)


async def try_create_group_call(
    chat_id: int,
    *,
    title: str | None = None,
    reason: str = "group_text_command",
) -> GroupCallModerationResult:
    """Start a group call through the configured helper user client."""
    if not raw_create_group_call_available():
        return _result(False, False, "raw_api_unavailable")
    return await _try_helper_group_call_operation(
        chat_id,
        lambda client: create_group_call(client, chat_id, title=title),
        source=f"{reason}_create",
    )


async def try_discard_group_call(
    chat_id: int,
    *,
    reason: str = "group_text_command",
) -> GroupCallModerationResult:
    """Discard the active group call through the configured helper user client."""
    if not raw_discard_group_call_available():
        return _result(False, False, "raw_api_unavailable")
    return await _try_helper_group_call_operation(
        chat_id,
        lambda client: discard_group_call(client, chat_id),
        source=f"{reason}_discard",
    )


async def try_invite_to_group_call(
    chat_id: int,
    user_ids: list[int],
    *,
    reason: str = "group_text_command",
) -> GroupCallModerationResult:
    """Invite users to the active group call through raw MTProto."""
    if not raw_invite_group_call_available():
        return _result(False, False, "raw_api_unavailable")
    return await _try_helper_group_call_operation(
        chat_id,
        lambda client: invite_to_group_call(client, chat_id, user_ids),
        source=f"{reason}_invite",
    )


async def try_edit_group_call_title(
    chat_id: int,
    title: str,
    *,
    reason: str = "group_text_command",
) -> GroupCallModerationResult:
    """Set the active group call title through raw MTProto."""
    if not raw_edit_group_call_title_available():
        return _result(False, False, "raw_api_unavailable")
    return await _try_helper_group_call_operation(
        chat_id,
        lambda client: edit_group_call_title(client, chat_id, title),
        source=f"{reason}_title",
    )


async def try_export_group_call_invite_link(
    chat_id: int,
    *,
    reason: str = "group_text_command",
) -> tuple[GroupCallModerationResult, str | None]:
    """Export the active group call invite link through raw MTProto."""
    if not raw_export_group_call_invite_available():
        return _result(False, False, "raw_api_unavailable"), None
    resolution = await resolve_group_call_helper(
        chat_id,
        require_admin=True,
        reason=f"{reason}_link",
    )
    if not resolution.ok:
        return _result_from_resolution(resolution), None
    try:
        result, link = await export_group_call_invite_link(resolution.client, chat_id)
        logger.info(
            "group_call_action result chat_id=%s helper_id=%s helper_user_id=%s source=%s reason=%s ok=%s",
            chat_id,
            resolution.helper_id,
            resolution.helper_user_id,
            f"{reason}_link",
            result.reason,
            result.ok,
        )
        return GroupCallModerationResult(
            ok=result.ok,
            supported=result.supported,
            reason=result.reason,
            error_class=result.error_class,
            helper_id=resolution.helper_id,
            helper_user_id=resolution.helper_user_id,
            binding_source=resolution.binding_source,
        ), link
    except Exception as exc:
        mapped = _map_error(exc)
        logger.info(
            "group_call_action helper failed chat_id=%s helper_id=%s helper_user_id=%s source=%s reason=%s err=%s",
            chat_id,
            resolution.helper_id,
            resolution.helper_user_id,
            f"{reason}_link",
            mapped,
            type(exc).__name__,
        )
        return _result(
            False,
            True,
            mapped,
            exc,
            helper_id=resolution.helper_id,
            helper_user_id=resolution.helper_user_id,
            binding_source=resolution.binding_source,
        ), None
    finally:
        await close_group_call_helper(resolution)


async def try_toggle_group_call_settings(
    chat_id: int,
    *,
    join_muted: bool | None = None,
    messages_enabled: bool | None = None,
    reason: str = "group_text_command",
) -> GroupCallModerationResult:
    """Apply live group-call settings through raw MTProto."""
    if not raw_toggle_group_call_settings_available():
        return _result(False, False, "raw_api_unavailable")
    return await _try_helper_group_call_operation(
        chat_id,
        lambda client: toggle_group_call_settings(
            client,
            chat_id,
            join_muted=join_muted,
            messages_enabled=messages_enabled,
        ),
        source=f"{reason}_settings",
    )
