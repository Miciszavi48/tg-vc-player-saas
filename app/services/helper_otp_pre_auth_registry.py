"""In-memory registry for live pre-auth Pyrogram clients during Helper OTP wizard.

Keeps the connected client between send_code and sign_in/check_password so we do
not rely on export_session_string before full authorization. Ephemeral: lost on
bot restart (user must restart OTP flow).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from app.utils.diagnostic_logging import create_logged_task, mask_phone
from app.utils.redis_keys import TTL_HELPER_OTP

if TYPE_CHECKING:
    from pyrogram import Client


@dataclass
class OtpPreAuthEntry:
    """Live pre-auth OTP client and metadata for one developer user."""

    client: Client
    phone: str
    phone_code_hash: str
    expires_at: float


_entries: dict[int, OtpPreAuthEntry] = {}


def _purge_expired() -> None:
    now = time.monotonic()
    expired = [uid for uid, entry in _entries.items() if entry.expires_at <= now]
    for user_id in expired:
        _evict_unlocked(user_id, phase="ttl_expired")


async def _disconnect_client(client: Client | None, *, user_id: int, phase: str) -> None:
    if client is None:
        return
    try:
        await client.disconnect()
        logger.debug(
            "helper_otp_registry disconnect user_id={} phase={} ok=True",
            user_id,
            phase,
        )
    except Exception as exc:
        logger.opt(exception=True).warning(
            "helper_otp_registry disconnect user_id={} phase={} exc={}",
            user_id,
            phase,
            type(exc).__name__,
        )


def _evict_unlocked(user_id: int, *, phase: str) -> None:
    entry = _entries.pop(user_id, None)
    if entry is not None:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            create_logged_task(
                _disconnect_client(entry.client, user_id=user_id, phase=phase),
                name=f"helper_otp_registry_disconnect_{user_id}",
            )
        except RuntimeError:
            pass


async def put(
    user_id: int,
    *,
    client: Client,
    phone: str,
    phone_code_hash: str,
    ttl_seconds: int = TTL_HELPER_OTP,
) -> None:
    """Store a live pre-auth client; replaces any existing entry for user_id."""
    _purge_expired()
    await evict(user_id, phase="replace")
    _entries[user_id] = OtpPreAuthEntry(
        client=client,
        phone=phone,
        phone_code_hash=phone_code_hash,
        expires_at=time.monotonic() + ttl_seconds,
    )
    logger.info(
        "helper_otp_registry put user_id={} phone_masked={} ttl_s={}",
        user_id,
        mask_phone(phone),
        ttl_seconds,
    )


def get(user_id: int) -> OtpPreAuthEntry | None:
    """Return a non-expired registry entry or None."""
    _purge_expired()
    entry = _entries.get(user_id)
    if entry is None:
        return None
    if entry.expires_at <= time.monotonic():
        _evict_unlocked(user_id, phase="ttl_expired_on_get")
        return None
    return entry


async def evict(user_id: int, *, phase: str = "evict") -> None:
    """Disconnect and remove the pre-auth client for user_id."""
    entry = _entries.pop(user_id, None)
    if entry is not None:
        await _disconnect_client(entry.client, user_id=user_id, phase=phase)
        logger.debug("helper_otp_registry evict user_id={} phase={}", user_id, phase)


def has(user_id: int) -> bool:
    """Return True when a live non-expired entry exists."""
    return get(user_id) is not None


def clear_all() -> None:
    """Evict every entry (tests only)."""
    for user_id in list(_entries.keys()):
        _evict_unlocked(user_id, phase="clear_all")


