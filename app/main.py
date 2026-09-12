from __future__ import annotations

import asyncio
import signal
import sys

from pyrogram import Client

from app.config.settings import settings
from app.database.engine import init_db, engine
from app.handlers import register_all
from app.scheduler import setup_scheduler
from app.utils.logger import setup_logger
from app.utils.diagnostic_logging import safe_exc_name
from app.utils.button_style import install_inline_keyboard_style_hook
from app.runtime.http_endpoints import OperationalEndpoints
from app.runtime.instance import InstanceRuntimeGuard

from loguru import logger

call_py = None

try:
    from pytgcalls import PyTgCalls  # noqa: F401
    _PYTGCALLS_AVAILABLE = True
except ImportError:
    _PYTGCALLS_AVAILABLE = False


async def _run_bot(
    runtime_guard: InstanceRuntimeGuard,
    endpoints: OperationalEndpoints,
) -> None:
    global call_py

    setup_logger()
    install_inline_keyboard_style_hook()
    logger.info(
        "Starting Telegram Music Player Bot instance_id={} root={}",
        settings.INSTANCE_ID,
        settings.INSTANCE_ROOT,
    )

    from app.services.bot_update_service import mark_process_started, set_runtime_refs

    mark_process_started()

    if not settings.BOT_TOKEN:
        logger.error("BOT_TOKEN is not set. Exiting.")
        sys.exit(1)
    if not settings.API_ID or not settings.API_HASH:
        logger.error("API_ID / API_HASH are not set. Exiting.")
        sys.exit(1)

    bot = Client(
        name=settings.BOT_SESSION_NAME,
        api_id=settings.API_ID,
        api_hash=settings.API_HASH,
        bot_token=settings.BOT_TOKEN,
        workdir=settings.SESSION_PATH,
    )

    if _PYTGCALLS_AVAILABLE:
        logger.info(
            "PyTgCalls available — helper user sessions will be started on demand"
        )
    else:
        logger.warning(
            "pytgcalls is not available — voice chat features will be disabled. "
            "Install pytgcalls with a compatible tgcalls build to enable streaming."
        )

    # Initialize database tables
    logger.info("Initializing database...")
    await init_db()
    await runtime_guard.acquire_distributed_leases()
    await endpoints.start()

    # Bootstrap: seed DEVELOPER_ID into DB on first run
    from app.bootstrap import bootstrap
    await bootstrap()

    # Register all handler modules
    register_all(bot, call_py)
    logger.info("All handlers registered")

    # Start the scheduler
    setup_scheduler(bot, call_py)
    set_runtime_refs(bot=bot, call_py=call_py)

    # Start the bot
    await bot.start()
    logger.info("Bot started as @{}", (await bot.get_me()).username)

    from app.handlers import apply_callback_safety_wrapper
    from app.handlers.callback_route_diag import log_callback_route_checks
    from app.handlers.help_center import verify_help_callback_router

    await apply_callback_safety_wrapper(bot)
    logger.info("Callback safety wrapper applied")
    try:
        await log_callback_route_checks(bot)
    except Exception as exc:
        logger.opt(exception=True).error(
            "Callback route diagnostics failed during startup; continuing exc={}",
            safe_exc_name(exc),
        )

    if not await verify_help_callback_router(bot):
        logger.error(
            "Help Center callback routing failed post-start verification — "
            "h:*:U{id} taps may hit unknown_callback fallback; see help_diag.verify logs"
        )

    if _PYTGCALLS_AVAILABLE:
        try:
            from app.services.recovery_service import schedule_recovery

            await schedule_recovery(None)
            logger.info("Playback recovery scheduled (helper PyTgCalls pool)")
        except Exception:
            logger.opt(exception=True).error(
                "Failed to schedule playback recovery — voice features may be degraded"
            )

    # Graceful shutdown handler
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()

    def _global_exception_handler(loop, context) -> None:
        exc = context.get("exception")
        msg = context.get("message", "")
        logger.error(
            "Unhandled async exception: {} exc={}",
            msg,
            safe_exc_name(exc),
        )
        if exc:
            try:
                asyncio.ensure_future(_notify_dev_error(bot, exc))
            except Exception:
                pass

    async def _notify_dev_error(bot_client, exc) -> None:
        try:
            from app.services.notification_service import NotificationService
            await NotificationService.notify_error(
                bot_client,
                f"Unhandled exception: {safe_exc_name(exc)}",
            )
        except Exception:
            logger.debug("Could not send error notification to log channel")

    loop.set_exception_handler(_global_exception_handler)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    # Block until shutdown
    await stop_event.wait()

    # Cleanup
    logger.info("Shutting down...")
    from app.scheduler import scheduler

    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

    # Persist active call states before stopping so recovery can resume them
    from app.services.call_service import _active_calls
    from app.services.seek_tracker import stop_seek_tracker

    active_count = len(_active_calls)
    if active_count:
        logger.info("Persisting {} active call states for recovery...", active_count)
        for chat_id in list(_active_calls.keys()):
            stop_seek_tracker(chat_id)

    if _PYTGCALLS_AVAILABLE:
        try:
            from app.services.helper_pytgcalls_pool import HelperPyTgCallsPool

            await HelperPyTgCallsPool.stop_all()
        except Exception:
            pass

    await bot.stop()
    logger.info("Bot stopped. {} calls saved for recovery. Goodbye!", active_count)


async def main() -> None:
    runtime_guard = InstanceRuntimeGuard(settings)
    endpoints = OperationalEndpoints(settings)
    runtime_guard.prepare_directories()
    runtime_guard.acquire_file_lock()
    try:
        await _run_bot(runtime_guard, endpoints)
    finally:
        await endpoints.stop()
        await runtime_guard.release()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
