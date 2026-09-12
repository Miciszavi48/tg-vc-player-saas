"""Add player deputies and VIP expiry.

Revision ID: 0027_player_deputies_vip_expiry
Revises: 0026_call_reports_helper_idx
Create Date: 2026-06-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027_player_deputies_vip_expiry"
down_revision = "0026_call_reports_helper_idx"
branch_labels = None
depends_on = None

TABLE_DEPUTIES = "player_deputies"
TABLE_VIPS = "player_vips"
VIP_EXPIRES_AT = "expires_at"


def _has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    if not _has_table(inspector, table_name):
        return False
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    if not _has_table(inspector, table_name):
        return False
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, TABLE_DEPUTIES):
        op.create_table(
            TABLE_DEPUTIES,
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("username", sa.String(length=128), nullable=True),
            sa.Column("display_name", sa.String(length=255), nullable=True),
            sa.Column("promoted_by", sa.BigInteger(), nullable=True),
            sa.Column(
                "promoted_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "chat_id",
                "user_id",
                name="uq_player_deputy_chat_user",
            ),
        )
        inspector = sa.inspect(bind)

    if not _has_index(inspector, TABLE_DEPUTIES, "ix_player_deputies_chat_id"):
        op.create_index("ix_player_deputies_chat_id", TABLE_DEPUTIES, ["chat_id"], unique=False)
    if not _has_index(inspector, TABLE_DEPUTIES, "ix_player_deputies_user_id"):
        op.create_index("ix_player_deputies_user_id", TABLE_DEPUTIES, ["user_id"], unique=False)
    if not _has_index(inspector, TABLE_DEPUTIES, "idx_player_deputies_chat"):
        op.create_index("idx_player_deputies_chat", TABLE_DEPUTIES, ["chat_id"], unique=False)
    if not _has_index(inspector, TABLE_DEPUTIES, "idx_player_deputies_user_chat"):
        op.create_index(
            "idx_player_deputies_user_chat",
            TABLE_DEPUTIES,
            ["user_id", "chat_id"],
            unique=False,
        )

    if _has_table(inspector, TABLE_VIPS) and not _has_column(inspector, TABLE_VIPS, VIP_EXPIRES_AT):
        op.add_column(TABLE_VIPS, sa.Column(VIP_EXPIRES_AT, sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_column(inspector, TABLE_VIPS, VIP_EXPIRES_AT):
        op.drop_column(TABLE_VIPS, VIP_EXPIRES_AT)

    inspector = sa.inspect(bind)
    if _has_table(inspector, TABLE_DEPUTIES):
        for index_name in (
            "idx_player_deputies_user_chat",
            "idx_player_deputies_chat",
            "ix_player_deputies_user_id",
            "ix_player_deputies_chat_id",
        ):
            inspector = sa.inspect(bind)
            if _has_index(inspector, TABLE_DEPUTIES, index_name):
                op.drop_index(index_name, table_name=TABLE_DEPUTIES)
        op.drop_table(TABLE_DEPUTIES)
