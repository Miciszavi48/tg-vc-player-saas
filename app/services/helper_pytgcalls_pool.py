"""Long-lived PyTgCalls instances bound to helper Telegram user sessions.

Playback and voice-control operations must use helper user MTProto sessions.
Bot accounts cannot create or join group calls (``BOT_METHOD_INVALID``).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.database.engine import async_session
from app.database.models import HelperChatBinding
from app.services.helper_pool_service import HelperPoolService
from app.utils.diagnostic_logging import safe_exc_name

logger = logging.getLogger(__name__)

_PYTGCALLS_AVAILABLE = False
try:
    from pytgcalls import PyTgCalls  # noqa: F401

    _PYTGCALLS_AVAILABLE = True
except ImportError:
    PyTgCalls = None  # type: ignore[misc, assignment]


@dataclass
class _HelperRuntime:
    """Started helper Pyrogram client and PyTgCalls wrapper."""

    helper_id: int
    client: Any
    call_py: Any
    stream_end_registered: bool = False


_POOL: dict[int, _HelperRuntime] = {}
_POOL_LOCK = asyncio.Lock()
_HELPER_START_LOCKS: dict[int, asyncio.Lock] = {}


def pytgcalls_available() -> bool:
    """Return whether the PyTgCalls package is importable."""
    return _PYTGCALLS_AVAILABLE


def call_py_is_bot_session(call_py: Any | None) -> bool:
    """Return True when *call_py* is bound to a Telegram bot account."""
    if call_py is None:
        return False
    app = getattr(call_py, "_app", None) or getattr(call_py, "client", None)
    if app is None:
        return False
    if getattr(app, "bot_token", None):
        bot_token = getattr(app, "bot_token", None)
        if isinstance(bot_token, str) and bot_token:
            return True
    me = getattr(app, "me", None)
    if me is not None:
        is_bot = getattr(me, "is_bot", None)
        if isinstance(is_bot, bool):
            return is_bot
    return False


async def _lookup_helper_id(chat_id: int) -> int | None:
    """Resolve helper id from in-memory active call state or chat binding."""
    from app.services.call_service import CallService

    active = CallService.get_active_calls().get(chat_id) or {}
    helper_id = active.get("helper_id")
    if helper_id is not None:
        return int(helper_id)

    async with async_session() as session:
        stmt = select(HelperChatBinding.helper_account_id).where(
            HelperChatBinding.chat_id == chat_id
        )
        result = await session.execute(stmt)
        bound = result.scalar_one_or_none()
    return int(bound) if bound is not None else None


def register_stream_end_handler(call_py: Any) -> None:
    """Wire pytgcalls stream-end events to ``CallService.play_next``."""
    if getattr(call_py, "_stream_end_registered", False):
        return

    from app.services import CallService

    async def _on_end(_client: Any, update: Any) -> None:
        chat_id = getattr(update, "chat_id", None)
        if chat_id is None:
            chat_id = getattr(update, "id", None)
        if chat_id is None:
            return
        try:
            if CallService.get_repeat_state(chat_id):
                active = CallService.get_active_calls().get(chat_id)
                if active:
                    source = active.get("source")
                    mtype = active.get("media_type", "audio")
                    if source:
                        await CallService.join_voice_chat(None, chat_id, source, mtype)
                        return
            await CallService.play_next(None, chat_id)
        except Exception:
            logger.exception("on_stream_end error for chat %s", chat_id)

    try:
        from pytgcalls import filters as fl

        @call_py.on_update(fl.stream_end())
        async def _stream_end_v2(_client: Any, update: Any) -> None:
            await _on_end(_client, update)

        call_py._stream_end_registered = True  # noqa: SLF001
        logger.info("Registered stream_end via fl.stream_end() (py-tgcalls 2.3+)")
        return
    except (ImportError, AttributeError, TypeError):
        pass

    try:
        @call_py.on_stream_end()
        async def _stream_end_v1(_client: Any, update: Any) -> None:
            await _on_end(_client, update)

        call_py._stream_end_registered = True  # noqa: SLF001
        logger.info("Registered stream_end via on_stream_end (pytgcalls v1)")
    except (AttributeError, TypeError):
        logger.warning("Could not register stream_end for helper PyTgCalls")


class HelperPyTgCallsPool:
    """Pool of per-helper PyTgCalls engines used for voice playback."""

    @staticmethod
    async def get_for_chat(
        chat_id: int,
        *,
        helper_id: int | None = None,
    ) -> Any | None:
        """Return a started PyTgCalls instance for *chat_id*'s helper."""
        hid = helper_id if helper_id is not None else await _lookup_helper_id(chat_id)
        if hid is None:
            return None
        return await HelperPyTgCallsPool.get_for_helper(int(hid))

    @staticmethod
    async def get_for_helper(helper_id: int) -> Any | None:
        """Return a started PyTgCalls instance for *helper_id*."""
        if not _PYTGCALLS_AVAILABLE:
            return None

        runtime = _POOL.get(helper_id)
        if runtime is not None:
            return runtime.call_py

        start_lock = _HELPER_START_LOCKS.setdefault(helper_id, asyncio.Lock())
        async with start_lock:
            runtime = _POOL.get(helper_id)
            if runtime is not None:
                return runtime.call_py
            return await _start_helper_runtime(helper_id)

    @staticmethod
    async def stop_helper(helper_id: int) -> None:
        """Stop and remove a helper runtime from the pool."""
        async with _POOL_LOCK:
            runtime = _POOL.pop(helper_id, None)
        if runtime is None:
            return
        await _stop_runtime(runtime)

    @staticmethod
    async def stop_all() -> None:
        """Stop every pooled helper PyTgCalls instance."""
        async with _POOL_LOCK:
            runtimes = list(_POOL.values())
            _POOL.clear()
        for runtime in runtimes:
            await _stop_runtime(runtime)

    @staticmethod
    def active_helper_ids() -> list[int]:
        """Return helper ids with a started pooled runtime."""
        return list(_POOL.keys())

    @staticmethod
    async def get_client_for_helper(helper_id: int) -> Any | None:
        """Return the started helper Pyrogram client for *helper_id*."""
        await HelperPyTgCallsPool.get_for_helper(helper_id)
        runtime = _POOL.get(helper_id)
        return runtime.client if runtime is not None else None


async def _start_helper_runtime(helper_id: int) -> Any | None:
    """Start a helper Pyrogram client and wrap it with PyTgCalls."""
    if PyTgCalls is None:
        return None

    session_str = await HelperPoolService.get_helper_session(helper_id)
    if session_str is None:
        logger.warning("helper PyTgCalls start failed helper_id=%s reason=no_session", helper_id)
        return None

    helper = await HelperPoolService._get_helper_row(helper_id)
    client = HelperPoolService.build_client(
        f"helper_vc_{helper_id}",
        session_str,
        helper,
    )
    call_py = PyTgCalls(client)

    try:
        await client.start()
        await call_py.start()
    except Exception as exc:
        logger.exception(
            "helper PyTgCalls start failed helper_id=%s exc=%s",
            helper_id,
            safe_exc_name(exc),
        )
        await _safe_stop_client(client)
        await _safe_stop_call_py(call_py)
        return None

    register_stream_end_handler(call_py)

    runtime = _HelperRuntime(
        helper_id=helper_id,
        client=client,
        call_py=call_py,
        stream_end_registered=True,
    )
    async with _POOL_LOCK:
        _POOL[helper_id] = runtime

    logger.info("helper PyTgCalls started helper_id=%s", helper_id)
    return call_py


async def _stop_runtime(runtime: _HelperRuntime) -> None:
    """Stop a pooled helper runtime."""
    await _safe_stop_call_py(runtime.call_py)
    await _safe_stop_client(runtime.client)
    logger.info("helper PyTgCalls stopped helper_id=%s", runtime.helper_id)


async def _safe_stop_call_py(call_py: Any) -> None:
    stop = getattr(call_py, "stop", None)
    if not callable(stop):
        return
    try:
        result = stop()
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        logger.debug("helper PyTgCalls stop failed", exc_info=True)


async def _safe_stop_client(client: Any) -> None:
    stop = getattr(client, "stop", None)
    if not callable(stop):
        return
    try:
        result = stop()
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        logger.debug("helper Pyrogram client stop failed", exc_info=True)
