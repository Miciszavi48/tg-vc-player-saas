"""Add per-chat owner-DM call-report flag (CALLSEC-01).

Revision ID: 0038_chat_call_report_dm
Revises: 0037_named_playlists
Create Date: 2026-08-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0038_chat_call_report_dm"
down_revision = "0037_named_playlists"
branch_labels = None
depends_on = None

TABLE = "chat_settings"
COLUMN = "call_report_dm_enabled"


def _has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    if not _has_table(inspector, table_name):
        return False
    return any(c["name"] == column_name for c in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _has_table(inspector, TABLE):
        return
    if not _has_column(inspector, TABLE, COLUMN):
        op.add_column(
            TABLE,
            sa.Column(COLUMN, sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _has_table(inspector, TABLE) and _has_column(inspector, TABLE, COLUMN):
        op.drop_column(TABLE, COLUMN)
