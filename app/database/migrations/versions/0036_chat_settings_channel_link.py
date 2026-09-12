"""Add group->channel playback link and routing toggle (CALLMGMT-01/03).

Revision ID: 0036_chat_settings_channel_link
Revises: 0035_chat_settings_service_clean
Create Date: 2026-08-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0036_chat_settings_channel_link"
down_revision = "0035_chat_settings_service_clean"
branch_labels = None
depends_on = None

TABLE = "chat_settings"
COL_LINKED_ID = "linked_channel_id"
COL_LINKED_TITLE = "linked_channel_title"
COL_ROUTING = "channel_playback_enabled"


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

    if not _has_column(inspector, TABLE, COL_LINKED_ID):
        op.add_column(TABLE, sa.Column(COL_LINKED_ID, sa.BigInteger(), nullable=True))
    if not _has_column(inspector, TABLE, COL_LINKED_TITLE):
        op.add_column(
            TABLE, sa.Column(COL_LINKED_TITLE, sa.String(length=255), nullable=True)
        )
    if not _has_column(inspector, TABLE, COL_ROUTING):
        op.add_column(
            TABLE,
            sa.Column(
                COL_ROUTING,
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
    for column in (COL_ROUTING, COL_LINKED_TITLE, COL_LINKED_ID):
        if _has_column(inspector, TABLE, column):
            op.drop_column(TABLE, column)
