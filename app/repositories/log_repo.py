from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update

from app.database.engine import async_session
from app.database.models import CallReport, InstallLog


async def log_install(
    chat_id: int,
    chat_title: str | None,
    chat_type: str,
    triggered_by: int | None,
    sudo_id: int | None,
    action: str,
) -> InstallLog:
    async with async_session() as session:
        log = InstallLog(
            chat_id=chat_id,
            chat_title=chat_title,
            chat_type=chat_type,
            triggered_by=triggered_by,
            sudo_id=sudo_id,
            action=action,
        )
        session.add(log)
        await session.commit()
        await session.refresh(log)
        return log


async def log_call(
    chat_id: int,
    chat_title: str | None,
    title: str | None,
    media_type: str,
    played_by: int | None,
    helper_account_id: int | None = None,
) -> int:
    async with async_session() as session:
        report = CallReport(
            chat_id=chat_id,
            chat_title=chat_title,
            title=title,
            media_type=media_type,
            played_by=played_by,
            helper_account_id=helper_account_id,
            started_at=datetime.now(timezone.utc),
        )
        session.add(report)
        await session.commit()
        await session.refresh(report)
        return report.id


async def end_call(call_report_id: int, duration_seconds: int) -> None:
    async with async_session() as session:
        stmt = (
            update(CallReport)
            .where(CallReport.id == call_report_id)
            .values(
                ended_at=datetime.now(timezone.utc),
                duration_seconds=duration_seconds,
            )
        )
        await session.execute(stmt)
        await session.commit()


async def get_install_logs(
    sudo_id: int | None = None, limit: int = 50
) -> list[InstallLog]:
    async with async_session() as session:
        stmt = select(InstallLog).order_by(InstallLog.occurred_at.desc()).limit(limit)
        if sudo_id is not None:
            stmt = stmt.where(InstallLog.sudo_id == sudo_id)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_install_logs_for_chat(
    chat_id: int,
    action: str | None = None,
    limit: int = 1,
    chat_type: str | None = None,
) -> list[InstallLog]:
    async with async_session() as session:
        stmt = (
            select(InstallLog)
            .where(InstallLog.chat_id == chat_id)
            .order_by(InstallLog.occurred_at.desc())
            .limit(limit)
        )
        if action is not None:
            stmt = stmt.where(InstallLog.action == action)
        if chat_type is not None:
            stmt = stmt.where(InstallLog.chat_type == chat_type)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_call_reports(
    chat_id: int | None = None, limit: int = 50
) -> list[CallReport]:
    async with async_session() as session:
        stmt = select(CallReport).order_by(CallReport.started_at.desc()).limit(limit)
        if chat_id is not None:
            stmt = stmt.where(CallReport.chat_id == chat_id)
        result = await session.execute(stmt)
        return list(result.scalars().all())
