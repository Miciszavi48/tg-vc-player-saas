"""Add per-chat auto-clear-on-stop flag and equalizer preset.

Revision ID: 0034_chat_auto_clear_equalizer
Revises: 0033_instance_database_ownership
Create Date: 2026-08-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034_chat_auto_clear_equalizer"
down_revision = "0033_instance_database_ownership"
branch_labels = None
depends_on = None

TABLE = "chat_settings"
COL_AUTO_CLEAR = "auto_clear_stopped_enabled"
COL_EQUALIZER = "equalizer_preset"


def _has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    if not _has_table(inspector, table_name):
        return False
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _has_table(inspector, TABLE):
        return

    if not _has_column(inspector, TABLE, COL_AUTO_CLEAR):
        op.add_column(
            TABLE,
            sa.Column(
                COL_AUTO_CLEAR,
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )

    if not _has_column(inspector, TABLE, COL_EQUALIZER):
        op.add_column(
            TABLE,
            sa.Column(
                COL_EQUALIZER,
                sa.String(length=16),
                nullable=False,
                server_default="normal",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _has_table(inspector, TABLE):
        return

    if _has_column(inspector, TABLE, COL_EQUALIZER):
        op.drop_column(TABLE, COL_EQUALIZER)
    if _has_column(inspector, TABLE, COL_AUTO_CLEAR):
        op.drop_column(TABLE, COL_AUTO_CLEAR)
