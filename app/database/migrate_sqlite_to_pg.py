#!/usr/bin/env python3
"""G6: SQLite → PostgreSQL migration script (§36.1).

Migrates data from the legacy single-file SQLite database to the new
PostgreSQL schema.  Safe to re-run (uses ON CONFLICT / upsert patterns).

Usage:
    python -m app.database.migrate_sqlite_to_pg \\
        --sqlite database.sqlite \\
        --pg "postgresql://musicbot:devpassword@localhost/musicbot_dev" \\
        [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
from datetime import datetime, timezone

import asyncpg


async def migrate(sqlite_path: str, pg_dsn: str, *, dry_run: bool = False) -> dict[str, int]:
    """Run the full migration.  Returns a dict of {table: rows_migrated}."""
    report: dict[str, int] = {}
    sq = sqlite3.connect(sqlite_path)
    sq.row_factory = sqlite3.Row
    cur = sq.cursor()

    pg = await asyncpg.connect(pg_dsn)

    try:
        report["users"] = await _migrate_users(cur, pg, dry_run)
        report["groups"] = await _migrate_groups(cur, pg, dry_run)
        report["channels"] = await _migrate_channels(cur, pg, dry_run)
        report["group_credits"] = await _migrate_credits(cur, pg, dry_run)
        report["sudos"] = await _migrate_sudos(cur, pg, dry_run)
        report["music_admins"] = await _migrate_music_admins(cur, pg, dry_run)
        report["video_admins"] = await _migrate_video_admins(cur, pg, dry_run)
        report["player_owners"] = await _migrate_player_owners(cur, pg, dry_run)
        report["bot_settings"] = await _migrate_bot_settings(cur, pg, dry_run)
    finally:
        await pg.close()
        sq.close()

    return report


async def _migrate_users(cur: sqlite3.Cursor, pg: asyncpg.Connection, dry_run: bool) -> int:
    try:
        cur.execute("SELECT iduser FROM users")
    except sqlite3.OperationalError:
        return 0
    rows = cur.fetchall()
    count = 0
    for r in rows:
        if dry_run:
            count += 1
            continue
        await pg.execute(
            "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
            r["iduser"],
        )
        count += 1
    return count


async def _migrate_groups(cur: sqlite3.Cursor, pg: asyncpg.Connection, dry_run: bool) -> int:
    try:
        cur.execute("SELECT namegp, idgp, linkgp, status FROM gp")
    except sqlite3.OperationalError:
        return 0
    rows = cur.fetchall()
    count = 0
    for r in rows:
        if dry_run:
            count += 1
            continue
        status = "active" if r["status"] == 1 else "inactive"
        await pg.execute(
            "INSERT INTO groups (chat_title, chat_id, invite_link, status) "
            "VALUES ($1, $2, $3, $4) ON CONFLICT (chat_id) DO NOTHING",
            r["namegp"], r["idgp"], r["linkgp"], status,
        )
        count += 1
    return count


async def _migrate_channels(cur: sqlite3.Cursor, pg: asyncpg.Connection, dry_run: bool) -> int:
    try:
        cur.execute("SELECT namech, idch, linkch, status FROM ch")
    except sqlite3.OperationalError:
        return 0
    rows = cur.fetchall()
    count = 0
    for r in rows:
        if dry_run:
            count += 1
            continue
        status = "active" if r["status"] == 1 else "inactive"
        await pg.execute(
            "INSERT INTO channels (chat_title, chat_id, invite_link, status) "
            "VALUES ($1, $2, $3, $4) ON CONFLICT (chat_id) DO NOTHING",
            r["namech"], r["idch"], r["linkch"], status,
        )
        count += 1
    return count


async def _migrate_credits(cur: sqlite3.Cursor, pg: asyncpg.Connection, dry_run: bool) -> int:
    try:
        cur.execute("SELECT idgp, idadmin, day, start, end, status FROM charge")
    except sqlite3.OperationalError:
        return 0
    rows = cur.fetchall()
    count = 0
    import time

    for r in rows:
        if dry_run:
            count += 1
            continue
        end_ts = r["end"] or 0
        credit_days = max(0, int((end_ts - time.time()) / 86400)) if end_ts else 0
        expire_at = datetime.fromtimestamp(end_ts, tz=timezone.utc) if end_ts else None
        status = "active" if r["status"] == 1 and credit_days > 0 else "expired"
        await pg.execute(
            "INSERT INTO group_credits (chat_id, chat_type, credit_days, expire_at, charged_by, status) "
            "VALUES ($1, 'group', $2, $3, $4, $5) ON CONFLICT (chat_id, chat_type) DO NOTHING",
            r["idgp"], credit_days, expire_at, r["idadmin"], status,
        )
        count += 1
    return count


async def _migrate_sudos(cur: sqlite3.Cursor, pg: asyncpg.Connection, dry_run: bool) -> int:
    try:
        cur.execute("SELECT idsudo, namesudo FROM sudo")
    except sqlite3.OperationalError:
        return 0
    rows = cur.fetchall()
    count = 0
    for r in rows:
        if dry_run:
            count += 1
            continue
        await pg.execute(
            "INSERT INTO sudos (user_id, display_name, added_by) "
            "VALUES ($1, $2, 0) ON CONFLICT (user_id) DO NOTHING",
            r["idsudo"], r["namesudo"],
        )
        count += 1
    return count


async def _migrate_music_admins(cur: sqlite3.Cursor, pg: asyncpg.Connection, dry_run: bool) -> int:
    try:
        cur.execute("SELECT idgp, idadmin, nameadmin FROM musicadmin")
    except sqlite3.OperationalError:
        return 0
    rows = cur.fetchall()
    count = 0
    for r in rows:
        if dry_run:
            count += 1
            continue
        await pg.execute(
            "INSERT INTO music_admins (chat_id, user_id, display_name) "
            "VALUES ($1, $2, $3) ON CONFLICT (chat_id, user_id) DO NOTHING",
            r["idgp"], r["idadmin"], r["nameadmin"],
        )
        count += 1
    return count


async def _migrate_video_admins(cur: sqlite3.Cursor, pg: asyncpg.Connection, dry_run: bool) -> int:
    try:
        cur.execute("SELECT idgp, idadmin, nameadmin FROM videoadmins")
    except sqlite3.OperationalError:
        return 0
    rows = cur.fetchall()
    count = 0
    for r in rows:
        if dry_run:
            count += 1
            continue
        await pg.execute(
            "INSERT INTO video_admins (chat_id, user_id, display_name) "
            "VALUES ($1, $2, $3) ON CONFLICT (chat_id, user_id) DO NOTHING",
            r["idgp"], r["idadmin"], r["nameadmin"],
        )
        count += 1
    return count


async def _migrate_player_owners(cur: sqlite3.Cursor, pg: asyncpg.Connection, dry_run: bool) -> int:
    try:
        cur.execute("SELECT idgp, creator FROM creators")
    except sqlite3.OperationalError:
        return 0
    rows = cur.fetchall()
    count = 0
    for r in rows:
        if dry_run:
            count += 1
            continue
        await pg.execute(
            "INSERT INTO player_owners (chat_id, user_id) "
            "VALUES ($1, $2) ON CONFLICT (chat_id, user_id) DO NOTHING",
            r["idgp"], r["creator"],
        )
        count += 1
    return count


async def _migrate_bot_settings(cur: sqlite3.Cursor, pg: asyncpg.Connection, dry_run: bool) -> int:
    try:
        cur.execute("SELECT start, about, groupp, adminpv, payamresan FROM information WHERE id=1")
    except sqlite3.OperationalError:
        return 0
    row = cur.fetchone()
    if row is None:
        return 0
    if dry_run:
        return 5

    mapping = {
        "start_text": row["start"],
        "about_text": row["about"],
        "support_group_link": f"https://t.me/{row['groupp']}" if row["groupp"] else "",
        "developer_pv_link": row["adminpv"] or "",
        "broadcast_channel_link": f"https://t.me/{row['payamresan']}" if row["payamresan"] else "",
    }
    count = 0
    for k, v in mapping.items():
        if v:
            await pg.execute(
                "UPDATE bot_settings SET value = $1, updated_at = NOW() WHERE key = $2",
                v, k,
            )
            count += 1
    return count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Migrate SQLite to PostgreSQL")
    parser.add_argument("--sqlite", required=True, help="Path to SQLite database file")
    parser.add_argument("--pg", required=True, help="PostgreSQL connection DSN")
    parser.add_argument("--dry-run", action="store_true", help="Count rows without writing")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    report = asyncio.run(migrate(args.sqlite, args.pg, dry_run=args.dry_run))

    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"\n{'=' * 50}")
    print(f"  {prefix}SQLite → PostgreSQL Migration Report")
    print(f"{'=' * 50}")
    total = 0
    for table, count in report.items():
        print(f"  {table:.<30} {count}")
        total += count
    print(f"{'─' * 50}")
    print(f"  {'Total':.<30} {total}")
    print()


if __name__ == "__main__":
    main()
