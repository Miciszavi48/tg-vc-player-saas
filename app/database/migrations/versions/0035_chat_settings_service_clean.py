"""Add per-chat service-message cleanup flag (PANEL-04).

Revision ID: 0035_chat_settings_service_clean
Revises: 0034_chat_auto_clear_equalizer
Create Date: 2026-08-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0035_chat_settings_service_clean"
down_revision = "0034_chat_auto_clear_equalizer"
branch_labels = None
depends_on = None

TABLE = "chat_settings"
COL_SERVICE_CLEAN = "service_clean_enabled"


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
    if not _has_column(inspector, TABLE, COL_SERVICE_CLEAN):
        op.add_column(
            TABLE,
            sa.Column(
                COL_SERVICE_CLEAN,
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _has_table(inspector, TABLE):
        return
    if _has_column(inspector, TABLE, COL_SERVICE_CLEAN):
        op.drop_column(TABLE, COL_SERVICE_CLEAN)
