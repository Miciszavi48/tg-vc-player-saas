from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import instance_session_name, settings
from app.database.engine import async_session
from app.database.models import HelperAccount, HelperChatBinding
from app.utils.diagnostic_logging import redact_freeform_text, safe_exc_name

if TYPE_CHECKING:
    from pyrogram import Client

logger = logging.getLogger(__name__)


def _helper_is_pool_eligible(helper: HelperAccount, now: datetime) -> bool:
    """Mirror SQL filters for pool selection (active, capacity, cooldown, ban)."""
    if helper.status != "active":
        return False
    if helper.current_active_calls >= helper.max_concurrent_calls:
        return False
    if helper.cooldown_until is not None and helper.cooldown_until > now:
        return False
    if helper.banned_until is not None and helper.banned_until > now:
        return False
    return True


def _helper_sort_key(helper: HelperAccount) -> tuple[int, int]:
    """Sort key for pool selection: lowest active calls, then stable id."""
    return (int(helper.current_active_calls), int(helper.id))


def _pool_eligible_filters(now: datetime):
    """SQL WHERE fragments shared by pool selection and reservation."""
    return (
        HelperAccount.status == "active",
        HelperAccount.current_active_calls < HelperAccount.max_concurrent_calls,
        (HelperAccount.cooldown_until.is_(None)) | (HelperAccount.cooldown_until <= now),
        (HelperAccount.banned_until.is_(None)) | (HelperAccount.banned_until <= now),
    )


@dataclass(frozen=True)
class ReservedHelper:
    """Helper row fields needed after an atomic reservation commit."""

    id: int
    phone: str
    status: str
    current_active_calls: int
    max_concurrent_calls: int


@dataclass(frozen=True)
class HelperJoinResult:
    ok: bool
    reason: str
    exception_type: str | None = None
    safe_message: str | None = None
    wait: int | None = None
    already_present: bool = False


def _safe_exception_message(exc: BaseException) -> str:
    return redact_freeform_text(str(exc))[:200]


def _is_valid_invite_link(link: str | None) -> bool:
    """Return True when *link* looks like a Telegram group/channel invite URL."""
    if not link:
        return False
    normalized = link.strip()
    if not normalized:
        return False
    return (
        normalized.startswith("https://t.me/")
        or normalized.startswith("http://t.me/")
        or normalized.startswith("tg://")
    )


def _normalize_invite_link(link: object | None) -> str | None:
    """Coerce kurigram export output to a plain invite URL string."""
    if link is None:
        return None
    if isinstance(link, str):
        candidate = link.strip()
    elif hasattr(link, "link"):
        candidate = str(getattr(link, "link", "")).strip()
    else:
        candidate = str(link).strip()
    return candidate if _is_valid_invite_link(candidate) else None


async def resolve_group_invite_link(
    bot_client: Client,
    chat_id: int,
    *,
    force_refresh: bool = False,
) -> str | None:
    """Resolve a group invite link via DB cache or bot ``export_chat_invite_link``.

    The main bot must be an admin with invite-link rights. Persisted links are
    reused unless *force_refresh* is True (e.g. expired hash).
    """
    from app.repositories import group_repo

    if not force_refresh:
        group = await group_repo.get_group(chat_id)
        cached = _normalize_invite_link(getattr(group, "invite_link", None) if group else None)
        if cached:
            return cached

    try:
        exported = await bot_client.export_chat_invite_link(chat_id)
        link = _normalize_invite_link(exported)
        if not link:
            logger.warning("bot export returned invalid invite chat_id=%s", chat_id)
            return None
        await group_repo.upsert_group(chat_id, invite_link=link)
        return link
    except Exception as exc:
        logger.warning(
            "failed to export group invite chat_id=%s exc=%s msg=%s",
            chat_id,
            safe_exc_name(exc),
            _safe_exception_message(exc),
        )
        return None


def _reserved_from_row(helper: HelperAccount, *, incremented: bool) -> ReservedHelper:
    calls = int(helper.current_active_calls)
    if incremented:
        calls += 1
    return ReservedHelper(
        id=int(helper.id),
        phone=str(helper.phone),
        status=str(helper.status),
        current_active_calls=calls,
        max_concurrent_calls=int(helper.max_concurrent_calls),
    )


class HelperPoolService:

    @staticmethod
    async def get_best_helper(chat_id: int) -> HelperAccount | None:
        async with async_session() as session:
            bound_stmt = select(HelperChatBinding).where(
                HelperChatBinding.chat_id == chat_id
            )
            result = await session.execute(bound_stmt)
            binding = result.scalar_one_or_none()

            if binding is not None:
                helper_stmt = select(HelperAccount).where(
                    HelperAccount.id == binding.helper_account_id,
                    HelperAccount.status == "active",
                )
                result = await session.execute(helper_stmt)
                helper = result.scalar_one_or_none()
                if helper is not None:
                    return helper

            now = datetime.now(timezone.utc)
            stmt = (
                select(HelperAccount)
                .where(*_pool_eligible_filters(now))
                .order_by(
                    HelperAccount.current_active_calls.asc(),
                    HelperAccount.id.asc(),
                )
                .limit(1)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    @staticmethod
    async def reserve_best_helper(chat_id: int) -> ReservedHelper | None:
        """Atomically pick a helper and increment ``current_active_calls``.

        Uses one short DB transaction with row-level locking. Does not perform
        Telegram/PyTgCalls work or create bindings.
        """
        now = datetime.now(timezone.utc)
        async with async_session() as session:
            async with session.begin():
                bound_stmt = select(HelperChatBinding).where(
                    HelperChatBinding.chat_id == chat_id
                )
                binding = (await session.execute(bound_stmt)).scalar_one_or_none()

                if binding is not None:
                    bound_stmt = (
                        select(HelperAccount)
                        .where(
                            HelperAccount.id == binding.helper_account_id,
                            *_pool_eligible_filters(now),
                        )
                        .with_for_update(skip_locked=True)
                    )
                    helper = (await session.execute(bound_stmt)).scalar_one_or_none()
                    if helper is not None:
                        await HelperPoolService._increment_helper_calls(
                            session, helper.id
                        )
                        await session.refresh(helper)
                        return _reserved_from_row(helper, incremented=False)

                pool_stmt = (
                    select(HelperAccount)
                    .where(*_pool_eligible_filters(now))
                    .order_by(
                        HelperAccount.current_active_calls.asc(),
                        HelperAccount.id.asc(),
                    )
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                helper = (await session.execute(pool_stmt)).scalar_one_or_none()
                if helper is None:
                    return None
                await HelperPoolService._increment_helper_calls(session, helper.id)
                await session.refresh(helper)
                return _reserved_from_row(helper, incremented=False)

    @staticmethod
    async def release_helper_reservation(helper_id: int) -> None:
        """Release a reservation when join/setup fails before an active call."""
        async with async_session() as session:
            async with session.begin():
                await session.execute(
                    update(HelperAccount)
                    .where(
                        HelperAccount.id == helper_id,
                        HelperAccount.current_active_calls > 0,
                    )
                    .values(
                        current_active_calls=HelperAccount.current_active_calls - 1
                    )
                )

    @staticmethod
    async def _increment_helper_calls(session: AsyncSession, helper_id: int) -> None:
        await session.execute(
            update(HelperAccount)
            .where(HelperAccount.id == helper_id)
            .values(current_active_calls=HelperAccount.current_active_calls + 1)
        )

    @staticmethod
    async def bind_chat_to_helper(chat_id: int, helper_id: int) -> None:
        async with async_session() as session:
            async with session.begin():
                stmt = select(HelperChatBinding).where(
                    HelperChatBinding.chat_id == chat_id
                )
                result = await session.execute(stmt)
                binding = result.scalar_one_or_none()

                if binding is None:
                    binding = HelperChatBinding(
                        chat_id=chat_id,
                        helper_account_id=helper_id,
                    )
                    session.add(binding)
                else:
                    binding.helper_account_id = helper_id

    @staticmethod
    async def release_chat(chat_id: int) -> None:
        from sqlalchemy import delete

        async with async_session() as session:
            async with session.begin():
                stmt = delete(HelperChatBinding).where(
                    HelperChatBinding.chat_id == chat_id
                )
                await session.execute(stmt)

    @staticmethod
    async def join_chat_as_helper(helper_id: int, chat_identifier: str) -> dict:
        """Join a chat using a helper's session. Returns {ok, chat_id, error}."""
        from app.utils.cache import acquire_lock, get_redis, release_lock

        session_str = await HelperPoolService.get_helper_session(helper_id)
        if session_str is None:
            return {"ok": False, "error": "no_session"}

        helper = await HelperPoolService._get_helper_row(helper_id)
        r = await get_redis()
        from app.utils.redis_keys import helper_join_lock_key
        lock_key = helper_join_lock_key(helper_id)
        token = await acquire_lock(lock_key, ttl_ms=60_000)
        if not token:
            return {"ok": False, "error": "lock_conflict"}

        try:
            client = HelperPoolService.build_client(
                f"helper_join_{helper_id}", session_str, helper)
            async with client:
                chat = await client.join_chat(chat_identifier)
                return {"ok": True, "chat_id": chat.id}
        except Exception as exc:
            exc_name = type(exc).__name__
            if "FloodWait" in exc_name:
                wait = getattr(exc, "value", 60)
                from app.utils.redis_keys import helper_floodwait_key
                await r.set(helper_floodwait_key(helper_id), "1", ex=int(wait) if isinstance(wait, (int, float)) else 60)
                return {"ok": False, "error": "flood_wait", "wait": wait}
            return {"ok": False, "error": str(exc)[:200]}
        finally:
            await release_lock(lock_key, token)

    @staticmethod
    async def leave_chat_as_helper(helper_id: int, chat_id: int) -> bool:
        """Leave a chat using a helper's session."""
        session_str = await HelperPoolService.get_helper_session(helper_id)
        if session_str is None:
            return False
        helper = await HelperPoolService._get_helper_row(helper_id)
        try:
            client = HelperPoolService.build_client(
                f"helper_leave_{helper_id}", session_str, helper)
            async with client:
                await client.leave_chat(chat_id)
                return True
        except Exception:
            logger.debug("Helper %d failed to leave chat %d", helper_id, chat_id)
            return False

    @staticmethod
    async def ensure_helper_joined(
        helper_id: int,
        chat_id: int,
        *,
        bot_client: Client | None = None,
    ) -> bool:
        """Check if helper is in the chat; if not, attempt to join."""
        result = await HelperPoolService.ensure_helper_joined_detailed(
            helper_id,
            chat_id,
            bot_client=bot_client,
        )
        return result.ok

    @staticmethod
    async def ensure_helper_joined_with_client(
        helper_id: int,
        chat_id: int,
        client: Client,
        *,
        bot_client: Client | None = None,
    ) -> HelperJoinResult:
        """Check/join a helper using an already-started Pyrogram client."""
        from app.services.bot_update_service import get_runtime_bot
        from app.utils.cache import get_redis
        from app.utils.redis_keys import helper_floodwait_key, helper_join_idem_key

        r = await get_redis()
        idem_key = helper_join_idem_key(helper_id, chat_id)
        if await r.get(idem_key):
            return HelperJoinResult(ok=True, reason="idempotent_cache", already_present=True)

        bot = bot_client or get_runtime_bot()
        if bot is None:
            return HelperJoinResult(ok=False, reason="bot_unavailable")

        async def _membership_ok(active_client: Client) -> bool:
            try:
                member = await active_client.get_chat_member(chat_id, "me")
                return member.status.value not in ("left", "kicked")
            except Exception as member_exc:
                logger.debug(
                    "helper membership precheck failed helper_id=%s chat_id=%s exc=%s",
                    helper_id,
                    chat_id,
                    safe_exc_name(member_exc),
                )
                return False

        async def _join_via_invite(
            active_client: Client,
            *,
            allow_invite_retry: bool,
        ) -> HelperJoinResult:
            invite_link = await resolve_group_invite_link(
                bot,
                chat_id,
                force_refresh=not allow_invite_retry,
            )
            if not invite_link:
                return HelperJoinResult(ok=False, reason="invite_link_failed")

            logger.info(
                "helper join invite prepared chat_id=%s invite_link=%s",
                chat_id,
                invite_link,
            )
            settle_seconds = max(0.0, float(settings.HELPER_JOIN_INVITE_SETTLE_SECONDS))
            logger.info(
                "helper join sleeping %ss before join chat_id=%s",
                settle_seconds,
                chat_id,
            )
            await asyncio.sleep(settle_seconds)

            try:
                await active_client.join_chat(invite_link)
            except Exception as exc:
                exc_name = safe_exc_name(exc)
                if exc_name == "UserAlreadyParticipant":
                    await r.set(idem_key, "1", ex=3600)
                    return HelperJoinResult(
                        ok=True,
                        reason="already_present",
                        already_present=True,
                    )
                if allow_invite_retry and exc_name in (
                    "InviteHashExpired",
                    "InviteHashInvalid",
                ):
                    logger.info(
                        "helper invite expired, refreshing chat_id=%s helper_id=%s",
                        chat_id,
                        helper_id,
                    )
                    return await _join_via_invite(active_client, allow_invite_retry=False)

                wait = getattr(exc, "value", None) if "FloodWait" in exc_name else None
                if wait is not None:
                    ttl = int(wait) if isinstance(wait, (int, float)) else 60
                    await r.set(helper_floodwait_key(helper_id), "1", ex=max(ttl, 1))
                if exc_name == "ChannelInvalid":
                    reason = "channel_invalid"
                elif wait is not None:
                    reason = "flood_wait"
                else:
                    reason = "join_failed"
                logger.debug(
                    "ensure_helper_joined failed helper_id=%s chat_id=%s exc=%s",
                    helper_id,
                    chat_id,
                    exc_name,
                )
                return HelperJoinResult(
                    ok=False,
                    reason=reason,
                    exception_type=exc_name,
                    safe_message=_safe_exception_message(exc),
                    wait=int(wait) if isinstance(wait, (int, float)) else None,
                )

            if await _membership_ok(active_client):
                logger.info(
                    "helper joined via invite link chat_id=%s helper_id=%s",
                    chat_id,
                    helper_id,
                )
                await r.set(idem_key, "1", ex=3600)
                return HelperJoinResult(ok=True, reason="joined")

            return HelperJoinResult(
                ok=False,
                reason="join_failed",
                safe_message="membership verify failed after join",
            )

        try:
            if await _membership_ok(client):
                await r.set(idem_key, "1", ex=3600)
                return HelperJoinResult(
                    ok=True,
                    reason="already_present",
                    already_present=True,
                )
            return await _join_via_invite(client, allow_invite_retry=True)
        except Exception as exc:
            exc_name = safe_exc_name(exc)
            wait = getattr(exc, "value", None) if "FloodWait" in exc_name else None
            if wait is not None:
                ttl = int(wait) if isinstance(wait, (int, float)) else 60
                await r.set(helper_floodwait_key(helper_id), "1", ex=max(ttl, 1))
            logger.debug(
                "ensure_helper_joined_with_client failed helper_id=%s chat_id=%s exc=%s",
                helper_id,
                chat_id,
                exc_name,
            )
            return HelperJoinResult(
                ok=False,
                reason="flood_wait" if wait is not None else "join_failed",
                exception_type=exc_name,
                safe_message=_safe_exception_message(exc),
                wait=int(wait) if isinstance(wait, (int, float)) else None,
            )

    @staticmethod
    async def ensure_helper_joined_detailed(
        helper_id: int,
        chat_id: int,
        *,
        bot_client: Client | None = None,
    ) -> HelperJoinResult:
        """Check/join a helper via bot-exported invite link and preserve failure reasons."""
        session_str = await HelperPoolService.get_helper_session(helper_id)
        if session_str is None:
            return HelperJoinResult(ok=False, reason="no_session")
        helper = await HelperPoolService._get_helper_row(helper_id)

        try:
            client = HelperPoolService.build_client(
                f"helper_ensure_{helper_id}", session_str, helper)
            async with client:
                return await HelperPoolService.ensure_helper_joined_with_client(
                    helper_id,
                    chat_id,
                    client,
                    bot_client=bot_client,
                )
        except Exception as exc:
            exc_name = safe_exc_name(exc)
            wait = getattr(exc, "value", None) if "FloodWait" in exc_name else None
            if wait is not None:
                from app.utils.cache import get_redis
                from app.utils.redis_keys import helper_floodwait_key

                r = await get_redis()
                ttl = int(wait) if isinstance(wait, (int, float)) else 60
                await r.set(helper_floodwait_key(helper_id), "1", ex=max(ttl, 1))
            logger.debug(
                "ensure_helper_joined session failed helper_id=%s chat_id=%s exc=%s",
                helper_id,
                chat_id,
                exc_name,
            )
            return HelperJoinResult(
                ok=False,
                reason="flood_wait" if wait is not None else "join_failed",
                exception_type=exc_name,
                safe_message=_safe_exception_message(exc),
                wait=int(wait) if isinstance(wait, (int, float)) else None,
            )

    @staticmethod
    async def quarantine_helper(
        helper_id: int,
        reason: str,
        duration_seconds: int = 3600,
    ) -> None:
        now = datetime.now(timezone.utc)
        cooldown_until = now + timedelta(seconds=duration_seconds)

        async with async_session() as session:
            async with session.begin():
                stmt = (
                    update(HelperAccount)
                    .where(HelperAccount.id == helper_id)
                    .values(
                        status="quarantined",
                        cooldown_until=cooldown_until,
                        last_error=reason,
                        last_error_at=now,
                        quarantine_count=HelperAccount.quarantine_count + 1,
                    )
                )
                await session.execute(stmt)

    @staticmethod
    async def activate_helper(helper_id: int) -> None:
        async with async_session() as session:
            async with session.begin():
                stmt = (
                    update(HelperAccount)
                    .where(HelperAccount.id == helper_id)
                    .values(
                        status="active",
                        cooldown_until=None,
                        banned_until=None,
                    )
                )
                await session.execute(stmt)

    @staticmethod
    async def get_helper_session(helper_id: int) -> str | None:
        async with async_session() as session:
            stmt = select(HelperAccount).where(HelperAccount.id == helper_id)
            result = await session.execute(stmt)
            helper = result.scalar_one_or_none()
            if helper is None or not helper.session_string_enc:
                return None

        return HelperPoolService.decrypt_session(helper.session_string_enc)

    @staticmethod
    def fingerprint_session(session_string: str) -> str:
        """Return a deterministic HMAC-SHA256 hex digest for duplicate detection."""
        key = settings.HELPER_SESSION_KEY_CURRENT
        if key:
            key_bytes = key.encode() if isinstance(key, str) else key
            return hmac.new(
                key_bytes,
                session_string.encode(),
                hashlib.sha256,
            ).hexdigest()
        return hashlib.sha256(session_string.encode()).hexdigest()

    @staticmethod
    async def is_duplicate_session(
        db_session: AsyncSession,
        session_string: str,
        *,
        exclude_helper_id: int | None = None,
    ) -> bool:
        """Return True if the plaintext session is already registered."""
        fingerprint = HelperPoolService.fingerprint_session(session_string)
        fp_stmt = select(HelperAccount.id).where(
            HelperAccount.session_fingerprint == fingerprint,
        )
        if exclude_helper_id is not None:
            fp_stmt = fp_stmt.where(HelperAccount.id != exclude_helper_id)
        fp_result = await db_session.execute(fp_stmt.limit(1))
        if fp_result.scalar_one_or_none() is not None:
            return True

        legacy_stmt = select(HelperAccount).where(
            HelperAccount.session_string_enc.is_not(None),
            HelperAccount.session_fingerprint.is_(None),
        )
        if exclude_helper_id is not None:
            legacy_stmt = legacy_stmt.where(HelperAccount.id != exclude_helper_id)
        legacy_result = await db_session.execute(legacy_stmt)
        for existing in legacy_result.scalars().all():
            enc_existing = existing.session_string_enc or ""
            if not enc_existing:
                continue
            plain_existing = HelperPoolService.decrypt_session(enc_existing)
            if plain_existing and plain_existing == session_string:
                return True
        return False

    @staticmethod
    def encrypt_session(session_string: str) -> str:
        key = settings.HELPER_SESSION_KEY_CURRENT
        if not key:
            raise ValueError("HELPER_SESSION_KEY_CURRENT is not configured")
        f = Fernet(key.encode() if isinstance(key, str) else key)
        return f.encrypt(session_string.encode()).decode()

    @staticmethod
    def decrypt_session(encrypted_text: str) -> str | None:
        token = encrypted_text.encode() if isinstance(encrypted_text, str) else encrypted_text

        current_key = settings.HELPER_SESSION_KEY_CURRENT
        if current_key:
            try:
                f = Fernet(current_key.encode() if isinstance(current_key, str) else current_key)
                return f.decrypt(token).decode()
            except InvalidToken:
                pass

        old_key = settings.HELPER_SESSION_KEY_OLD
        if old_key:
            try:
                f = Fernet(old_key.encode() if isinstance(old_key, str) else old_key)
                return f.decrypt(token).decode()
            except InvalidToken:
                pass

        logger.error("Failed to decrypt session string with any available key")
        return None

    @staticmethod
    async def increment_active_calls(chat_id: int) -> None:
        """Increment the active-call counter for the helper bound to *chat_id*."""
        async with async_session() as session:
            async with session.begin():
                binding_stmt = select(HelperChatBinding).where(
                    HelperChatBinding.chat_id == chat_id
                )
                result = await session.execute(binding_stmt)
                binding = result.scalar_one_or_none()
                if binding is None:
                    return
                await session.execute(
                    update(HelperAccount)
                    .where(HelperAccount.id == binding.helper_account_id)
                    .values(current_active_calls=HelperAccount.current_active_calls + 1)
                )

    @staticmethod
    async def decrement_active_calls(chat_id: int) -> None:
        """Decrement the active-call counter for the helper bound to *chat_id*.
        Clamps to zero to prevent negative drift."""
        async with async_session() as session:
            async with session.begin():
                binding_stmt = select(HelperChatBinding).where(
                    HelperChatBinding.chat_id == chat_id
                )
                result = await session.execute(binding_stmt)
                binding = result.scalar_one_or_none()
                if binding is None:
                    return
                await session.execute(
                    update(HelperAccount)
                    .where(
                        HelperAccount.id == binding.helper_account_id,
                        HelperAccount.current_active_calls > 0,
                    )
                    .values(current_active_calls=HelperAccount.current_active_calls - 1)
                )

    @staticmethod
    def build_client(
        name: str,
        session_string: str,
        helper: HelperAccount | None = None,
    ):
        """Build a Pyrogram Client with the helper's bound fingerprint and proxy."""
        from pyrogram import Client

        kwargs: dict = {
            "name": instance_session_name(name),
            "api_id": settings.API_ID,
            "api_hash": settings.API_HASH,
            "session_string": session_string,
            "in_memory": True,
        }

        if helper and helper.device_model:
            kwargs["device_model"] = helper.device_model
        if helper and helper.system_version:
            kwargs["system_version"] = helper.system_version
        if helper and helper.app_version:
            kwargs["app_version"] = helper.app_version
        if helper and helper.lang_code:
            kwargs["lang_code"] = helper.lang_code

        if helper and helper.proxy_type and helper.proxy_host and helper.proxy_port:
            proxy = {
                "scheme": helper.proxy_type.lower(),
                "hostname": helper.proxy_host,
                "port": helper.proxy_port,
            }
            if helper.proxy_username:
                proxy["username"] = helper.proxy_username
            if helper.proxy_password:
                proxy["password"] = helper.proxy_password
            kwargs["proxy"] = proxy

        return Client(**kwargs)

    @staticmethod
    async def _get_helper_row(helper_id: int) -> HelperAccount | None:
        async with async_session() as session:
            stmt = select(HelperAccount).where(HelperAccount.id == helper_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    @staticmethod
    async def get_all_helpers() -> list[HelperAccount]:
        async with async_session() as session:
            stmt = select(HelperAccount).order_by(HelperAccount.id.asc())
            result = await session.execute(stmt)
            return list(result.scalars().all())
