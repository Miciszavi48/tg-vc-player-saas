"""Add encrypted global YouTube cookie-session storage.

Revision ID: 0030_youtube_cookie_session_pool
Revises: 0029_start_customization_schema
Create Date: 2026-07-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0030_youtube_cookie_session_pool"
down_revision = "0029_start_customization_schema"
branch_labels = None
depends_on = None

_SESSIONS = "youtube_cookie_sessions"
_EVENTS = "youtube_cookie_session_events"


def _has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return _has_table(inspector, table_name) and any(
        index["name"] == index_name for index in inspector.get_indexes(table_name)
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, _SESSIONS):
        op.create_table(
            _SESSIONS,
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("cookie_blob_enc", sa.Text(), nullable=False),
            sa.Column("fingerprint", sa.String(length=64), nullable=False),
            sa.Column(
                "status",
                sa.String(length=16),
                server_default=sa.text("'disabled'"),
                nullable=False,
            ),
            sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_selected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error_code", sa.String(length=64), nullable=True),
            sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("use_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("failure_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("created_by", sa.BigInteger(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.CheckConstraint(
                "status IN ('active', 'disabled', 'invalid')",
                name="ck_youtube_cookie_sessions_status",
            ),
            sa.CheckConstraint("use_count >= 0", name="ck_youtube_cookie_sessions_use_count"),
            sa.CheckConstraint(
                "failure_count >= 0",
                name="ck_youtube_cookie_sessions_failure_count",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("fingerprint", name="uq_youtube_cookie_sessions_fingerprint"),
        )
        inspector = sa.inspect(bind)

    if not _has_index(inspector, _SESSIONS, "idx_youtube_cookie_sessions_rotation"):
        op.create_index(
            "idx_youtube_cookie_sessions_rotation",
            _SESSIONS,
            ["status", "cooldown_until", "last_selected_at", "id"],
            unique=False,
        )

    inspector = sa.inspect(bind)
    if not _has_table(inspector, _EVENTS):
        op.create_table(
            _EVENTS,
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("session_id", sa.Integer(), nullable=True),
            sa.Column("actor_id", sa.BigInteger(), nullable=True),
            sa.Column("action", sa.String(length=32), nullable=False),
            sa.Column("result_code", sa.String(length=64), nullable=True),
            sa.Column(
                "occurred_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        inspector = sa.inspect(bind)

    if not _has_index(inspector, _EVENTS, "idx_youtube_cookie_session_events_session_at"):
        op.create_index(
            "idx_youtube_cookie_session_events_session_at",
            _EVENTS,
            ["session_id", "occurred_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _has_table(inspector, _EVENTS):
        op.drop_table(_EVENTS)
    inspector = sa.inspect(bind)
    if _has_table(inspector, _SESSIONS):
        op.drop_table(_SESSIONS)
