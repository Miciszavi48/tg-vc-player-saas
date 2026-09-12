"""CLI tool for managing the helper account pool.

Usage:
    python -m app.tools.helper_pool_cli add --phone "+989123456789"
    python -m app.tools.helper_pool_cli list [--all]
    python -m app.tools.helper_pool_cli show --id 3
    python -m app.tools.helper_pool_cli disable --id 3
    python -m app.tools.helper_pool_cli enable --id 3
    python -m app.tools.helper_pool_cli delete --id 3 [--force]
    python -m app.tools.helper_pool_cli set-capacity --id 3 --max-calls 100
    python -m app.tools.helper_pool_cli test --id 3
    python -m app.tools.helper_pool_cli import-session --id 3 --session "BQ..."
    python -m app.tools.helper_pool_cli export-session --id 3 --i-know-what-im-doing
    python -m app.tools.helper_pool_cli add-batch --file helpers.json
    python -m app.tools.helper_pool_cli rotate-chat --chat-id -1001234 --target-helper-id 5
    python -m app.tools.helper_pool_cli rotate-key
    python -m app.tools.helper_pool_cli quarantine --id 3 --minutes 60 --reason "FloodWait"
    python -m app.tools.helper_pool_cli unquarantine --id 3
    python -m app.tools.helper_pool_cli list-events [--helper-id 3] [--type helper.add] [--limit 50]
    python -m app.tools.helper_pool_cli purge-events --older-than-days 90 --confirm
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete as sa_delete, func, select, update

from app.config.settings import instance_session_name, settings
from app.database.engine import async_session
from app.database.models import HelperAccount, HelperChatBinding, HelperEvent
from app.repositories import helper_event_repo
from app.services.helper_pool_service import HelperPoolService
from app.utils.helpers import mask_phone


async def _cmd_add(args: argparse.Namespace) -> None:
    phone = args.phone.strip()
    session_string = input("Paste the session string (or press Enter to skip): ").strip() if not args.no_prompt else ""
    enc_session = HelperPoolService.encrypt_session(session_string) if session_string else None
    session_fp = (
        HelperPoolService.fingerprint_session(session_string) if session_string else None
    )

    async with async_session() as session:
        async with session.begin():
            existing = await session.execute(
                select(HelperAccount).where(HelperAccount.phone == phone)
            )
            if existing.scalar_one_or_none() is not None:
                print(json.dumps({"ok": False, "error": "duplicate_phone"}))
                sys.exit(1)
            if session_string and await HelperPoolService.is_duplicate_session(
                session, session_string,
            ):
                print(json.dumps({"ok": False, "error": "duplicate_session"}))
                sys.exit(1)
            helper = HelperAccount(
                phone=phone,
                session_string_enc=enc_session,
                session_fingerprint=session_fp,
                status="active",
                max_concurrent_calls=settings.HELPER_DEFAULT_MAX_CALLS,
                max_joins_per_hour=settings.HELPER_DEFAULT_MAX_JOINS_PER_HOUR,
            )
            session.add(helper)
            await session.flush()
            hid = helper.id
    await helper_event_repo.log_event("helper.add", actor="cli", helper_account_id=hid)
    print(json.dumps({"ok": True, "helper_id": hid, "phone": mask_phone(phone)}))


async def _cmd_list(args: argparse.Namespace) -> None:
    async with async_session() as session:
        result = await session.execute(select(HelperAccount).order_by(HelperAccount.id.asc()))
        helpers = list(result.scalars().all())
    for h in helpers:
        if not args.all and h.status != "active":
            continue
        print(json.dumps({
            "id": h.id, "phone": mask_phone(h.phone), "status": h.status,
            "calls": h.current_active_calls, "max_calls": h.max_concurrent_calls,
            "quarantine_count": h.quarantine_count,
        }))


async def _cmd_show(args: argparse.Namespace) -> None:
    async with async_session() as session:
        result = await session.execute(select(HelperAccount).where(HelperAccount.id == args.id))
        h = result.scalar_one_or_none()
    if h is None:
        print(json.dumps({"ok": False, "error": "not_found"}))
        sys.exit(1)
    print(json.dumps({
        "id": h.id, "phone": mask_phone(h.phone), "status": h.status,
        "tg_user_id": h.tg_user_id, "username": h.username, "display_name": h.display_name,
        "calls": h.current_active_calls, "max_calls": h.max_concurrent_calls,
        "quarantine_count": h.quarantine_count,
        "last_error": h.last_error, "cooldown_until": str(h.cooldown_until) if h.cooldown_until else None,
    }))


async def _cmd_disable(args: argparse.Namespace) -> None:
    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                update(HelperAccount).where(HelperAccount.id == args.id).values(status="disabled")
            )
    await helper_event_repo.log_event("helper.disable", actor="cli", helper_account_id=args.id)
    print(json.dumps({"ok": result.rowcount > 0, "helper_id": args.id, "action": "disabled"}))


async def _cmd_enable(args: argparse.Namespace) -> None:
    await HelperPoolService.activate_helper(args.id)
    await helper_event_repo.log_event("helper.enable", actor="cli", helper_account_id=args.id)
    print(json.dumps({"ok": True, "helper_id": args.id, "action": "enabled"}))


async def _cmd_delete(args: argparse.Namespace) -> None:
    async with async_session() as session:
        bindings = await session.execute(
            select(func.count()).select_from(HelperChatBinding)
            .where(HelperChatBinding.helper_account_id == args.id)
        )
        count = bindings.scalar() or 0
    if count > 0 and not args.force:
        print(json.dumps({"ok": False, "error": "has_bindings", "binding_count": count, "hint": "use --force"}))
        sys.exit(1)
    if count > 0:
        async with async_session() as session:
            async with session.begin():
                await session.execute(
                    sa_delete(HelperChatBinding).where(HelperChatBinding.helper_account_id == args.id)
                )
    async with async_session() as session:
        async with session.begin():
            await session.execute(
                update(HelperAccount).where(HelperAccount.id == args.id).values(status="deleted")
            )
    await helper_event_repo.log_event("helper.delete", actor="cli", helper_account_id=args.id)
    print(json.dumps({"ok": True, "helper_id": args.id, "action": "deleted", "bindings_released": count}))


async def _cmd_set_capacity(args: argparse.Namespace) -> None:
    if args.max_calls < 1 or args.max_calls > 1000:
        print(json.dumps({"ok": False, "error": "invalid_range", "hint": "1-1000"}))
        sys.exit(1)
    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                update(HelperAccount).where(HelperAccount.id == args.id)
                .values(max_concurrent_calls=args.max_calls)
            )
    print(json.dumps({"ok": result.rowcount > 0, "helper_id": args.id, "max_calls": args.max_calls}))


async def _cmd_test(args: argparse.Namespace) -> None:
    session_str = await HelperPoolService.get_helper_session(args.id)
    if session_str is None:
        print(json.dumps({"ok": False, "error": "no_session"}))
        sys.exit(1)
    try:
        from pyrogram import Client
        client = Client(
            name=instance_session_name(f"helper_test_{args.id}"),
            api_id=settings.API_ID,
            api_hash=settings.API_HASH, session_string=session_str, in_memory=True,
        )
        async with client:
            me = await client.get_me()
            async with async_session() as session:
                async with session.begin():
                    await session.execute(
                        update(HelperAccount).where(HelperAccount.id == args.id)
                        .values(tg_user_id=me.id, username=me.username, display_name=me.first_name)
                    )
            print(json.dumps({"ok": True, "tg_id": me.id, "username": me.username, "name": me.first_name}))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:200]}))
        sys.exit(1)


async def _cmd_import_session(args: argparse.Namespace) -> None:
    enc = HelperPoolService.encrypt_session(args.session)
    session_fp = HelperPoolService.fingerprint_session(args.session)
    async with async_session() as session:
        async with session.begin():
            if await HelperPoolService.is_duplicate_session(
                session, args.session, exclude_helper_id=args.id,
            ):
                print(json.dumps({"ok": False, "error": "duplicate_session"}))
                sys.exit(1)
            result = await session.execute(
                update(HelperAccount).where(HelperAccount.id == args.id)
                .values(session_string_enc=enc, session_fingerprint=session_fp)
            )
    await helper_event_repo.log_event("helper.import_session", actor="cli", helper_account_id=args.id)
    print(json.dumps({"ok": result.rowcount > 0, "helper_id": args.id, "action": "session_imported"}))


async def _cmd_export_session(args: argparse.Namespace) -> None:
    if not args.i_know_what_im_doing:
        print(json.dumps({"ok": False, "error": "requires --i-know-what-im-doing flag"}))
        sys.exit(1)
    session_str = await HelperPoolService.get_helper_session(args.id)
    if session_str is None:
        print(json.dumps({"ok": False, "error": "no_session_or_decrypt_failed"}))
        sys.exit(1)
    print(session_str)


async def _cmd_add_batch(args: argparse.Namespace) -> None:
    with open(args.file) as f:
        entries = json.load(f)
    if not isinstance(entries, list):
        print(json.dumps({"ok": False, "error": "file must contain a JSON array"}))
        sys.exit(1)
    added = skipped = errors = 0
    for entry in entries:
        phone = entry.get("phone", "").strip()
        if not phone:
            errors += 1
            continue
        session_plain = entry.get("string_session") or ""
        enc = HelperPoolService.encrypt_session(session_plain) if session_plain else None
        session_fp = (
            HelperPoolService.fingerprint_session(session_plain) if session_plain else None
        )
        try:
            async with async_session() as session:
                async with session.begin():
                    existing = await session.execute(select(HelperAccount).where(HelperAccount.phone == phone))
                    if existing.scalar_one_or_none():
                        skipped += 1
                        continue
                    if session_plain and await HelperPoolService.is_duplicate_session(
                        session, session_plain,
                    ):
                        skipped += 1
                        continue
                    helper = HelperAccount(
                        phone=phone,
                        session_string_enc=enc,
                        session_fingerprint=session_fp,
                        status="active",
                        display_name=entry.get("display_name"),
                        max_concurrent_calls=entry.get("max_calls", settings.HELPER_DEFAULT_MAX_CALLS),
                        max_joins_per_hour=settings.HELPER_DEFAULT_MAX_JOINS_PER_HOUR,
                    )
                    session.add(helper)
                    await session.flush()
                    await helper_event_repo.log_event("helper.add", actor="cli", helper_account_id=helper.id)
            added += 1
        except Exception:
            errors += 1
    print(json.dumps({"ok": True, "added": added, "skipped": skipped, "errors": errors}))


async def _cmd_rotate_chat(args: argparse.Namespace) -> None:
    await HelperPoolService.bind_chat_to_helper(args.chat_id, args.target_helper_id)
    await helper_event_repo.log_event("helper.bind", actor="cli",
                                       helper_account_id=args.target_helper_id, chat_id=args.chat_id)
    print(json.dumps({"ok": True, "chat_id": args.chat_id, "helper_id": args.target_helper_id}))


async def _cmd_rotate_key(args: argparse.Namespace) -> None:
    if not settings.HELPER_SESSION_KEY_CURRENT:
        print(json.dumps({"ok": False, "error": "HELPER_SESSION_KEY_CURRENT not set"}))
        sys.exit(1)
    helpers = await HelperPoolService.get_all_helpers()
    rotated = errors = 0
    for helper in helpers:
        if not helper.session_string_enc:
            continue
        try:
            plain = HelperPoolService.decrypt_session(helper.session_string_enc)
            if plain is None:
                errors += 1
                continue
            new_enc = HelperPoolService.encrypt_session(plain)
            async with async_session() as session:
                async with session.begin():
                    await session.execute(
                        update(HelperAccount).where(HelperAccount.id == helper.id)
                        .values(session_string_enc=new_enc)
                    )
            rotated += 1
        except Exception:
            errors += 1
    await helper_event_repo.log_event("helper.rotate_key", actor="cli", metadata={"rotated": rotated, "errors": errors})
    print(json.dumps({"ok": True, "rotated": rotated, "errors": errors}))


async def _cmd_quarantine(args: argparse.Namespace) -> None:
    await HelperPoolService.quarantine_helper(args.id, args.reason, args.minutes * 60)
    await helper_event_repo.log_event("helper.quarantine", actor="cli",
                                       helper_account_id=args.id, metadata={"reason": args.reason, "minutes": args.minutes})
    print(json.dumps({"ok": True, "helper_id": args.id, "action": "quarantined", "minutes": args.minutes}))


async def _cmd_unquarantine(args: argparse.Namespace) -> None:
    await HelperPoolService.activate_helper(args.id)
    await helper_event_repo.log_event("helper.unquarantine", actor="cli", helper_account_id=args.id)
    print(json.dumps({"ok": True, "helper_id": args.id, "action": "unquarantined"}))


async def _cmd_list_events(args: argparse.Namespace) -> None:
    events = await helper_event_repo.get_events(
        helper_id=args.helper_id, event_type=args.type, limit=args.limit,
    )
    for ev in events:
        print(json.dumps({
            "id": ev.id, "type": ev.event_type, "actor": ev.actor,
            "helper_id": ev.helper_account_id, "chat_id": ev.chat_id,
            "metadata": ev.metadata_json, "at": str(ev.created_at),
        }))


async def _cmd_purge_events(args: argparse.Namespace) -> None:
    if not args.confirm:
        print(json.dumps({"ok": False, "error": "requires --confirm flag"}))
        sys.exit(1)
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.older_than_days)
    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                sa_delete(HelperEvent).where(HelperEvent.created_at < cutoff)
            )
    print(json.dumps({"ok": True, "deleted": result.rowcount, "older_than_days": args.older_than_days}))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="helper_pool_cli", description="Manage the Telegram helper account pool")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add", help="Add a new helper account")
    p.add_argument("--phone", required=True)
    p.add_argument("--no-prompt", action="store_true", help="Skip session string prompt")

    p = sub.add_parser("list", help="List helper accounts")
    p.add_argument("--all", action="store_true")

    p = sub.add_parser("show", help="Show helper details")
    p.add_argument("--id", type=int, required=True)

    p = sub.add_parser("disable", help="Disable a helper")
    p.add_argument("--id", type=int, required=True)

    p = sub.add_parser("enable", help="Enable a helper")
    p.add_argument("--id", type=int, required=True)

    p = sub.add_parser("delete", help="Soft-delete a helper")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--force", action="store_true", help="Release bindings and delete")

    p = sub.add_parser("set-capacity", help="Set max concurrent calls")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--max-calls", type=int, required=True)

    p = sub.add_parser("test", help="Test helper login")
    p.add_argument("--id", type=int, required=True)

    p = sub.add_parser("import-session", help="Import and encrypt a session string")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--session", required=True, help="Session string to import")

    p = sub.add_parser("export-session", help="Decrypt and export a session string")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--i-know-what-im-doing", action="store_true")

    p = sub.add_parser("add-batch", help="Add helpers from a JSON file")
    p.add_argument("--file", required=True, help="Path to JSON file")

    p = sub.add_parser("rotate-chat", help="Reassign a chat to a helper")
    p.add_argument("--chat-id", type=int, required=True)
    p.add_argument("--target-helper-id", type=int, required=True)

    sub.add_parser("rotate-key", help="Re-encrypt all sessions with current key")

    p = sub.add_parser("quarantine", help="Quarantine a helper")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--minutes", type=int, required=True)
    p.add_argument("--reason", required=True)

    p = sub.add_parser("unquarantine", help="Remove quarantine")
    p.add_argument("--id", type=int, required=True)

    p = sub.add_parser("list-events", help="List helper audit events")
    p.add_argument("--helper-id", type=int, default=None)
    p.add_argument("--type", default=None)
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("purge-events", help="Delete old events")
    p.add_argument("--older-than-days", type=int, required=True)
    p.add_argument("--confirm", action="store_true")

    return parser


_DISPATCH = {
    "add": _cmd_add, "list": _cmd_list, "show": _cmd_show,
    "disable": _cmd_disable, "enable": _cmd_enable, "delete": _cmd_delete,
    "set-capacity": _cmd_set_capacity, "test": _cmd_test,
    "import-session": _cmd_import_session, "export-session": _cmd_export_session,
    "add-batch": _cmd_add_batch,
    "rotate-chat": _cmd_rotate_chat, "rotate-key": _cmd_rotate_key,
    "quarantine": _cmd_quarantine, "unquarantine": _cmd_unquarantine,
    "list-events": _cmd_list_events, "purge-events": _cmd_purge_events,
}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    handler = _DISPATCH.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)
    asyncio.run(handler(args))


if __name__ == "__main__":
    main()
