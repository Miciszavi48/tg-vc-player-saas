"""Add monthly creator invoice records.

Revision ID: 0028_monthly_invoices
Revises: 0027_player_deputies_vip_expiry
Create Date: 2026-07-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0028_monthly_invoices"
down_revision = "0027_player_deputies_vip_expiry"
branch_labels = None
depends_on = None

TABLE_NAME = "monthly_invoices"
UQ_OWNER_PERIOD = "uq_monthly_invoices_owner_period"
IDX_OWNER_USER_ID = "ix_monthly_invoices_owner_user_id"
IDX_DUE_AT = "ix_monthly_invoices_due_at"
IDX_STATUS = "ix_monthly_invoices_status"
IDX_STATUS_DUE = "idx_monthly_invoices_status_due"
IDX_OWNER_CREATED = "idx_monthly_invoices_owner_created"


def _has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    if not _has_table(inspector, table_name):
        return False
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, TABLE_NAME):
        op.create_table(
            TABLE_NAME,
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("owner_user_id", sa.BigInteger(), nullable=False),
            sa.Column("bot_identifier", sa.String(length=255), nullable=False),
            sa.Column("period_start", sa.Date(), nullable=False),
            sa.Column("period_end", sa.Date(), nullable=False),
            sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("amount", sa.BigInteger(), nullable=False),
            sa.Column(
                "status",
                sa.String(length=32),
                server_default=sa.text("'pending'"),
                nullable=False,
            ),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("install_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("private_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("group_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("channel_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("developer_id", sa.BigInteger(), nullable=True),
            sa.Column("delivery_error", sa.Text(), nullable=True),
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
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "owner_user_id",
                "period_start",
                "period_end",
                name=UQ_OWNER_PERIOD,
            ),
        )
        inspector = sa.inspect(bind)

    if not _has_index(inspector, TABLE_NAME, IDX_OWNER_USER_ID):
        op.create_index(IDX_OWNER_USER_ID, TABLE_NAME, ["owner_user_id"], unique=False)
    if not _has_index(inspector, TABLE_NAME, IDX_DUE_AT):
        op.create_index(IDX_DUE_AT, TABLE_NAME, ["due_at"], unique=False)
    if not _has_index(inspector, TABLE_NAME, IDX_STATUS):
        op.create_index(IDX_STATUS, TABLE_NAME, ["status"], unique=False)
    if not _has_index(inspector, TABLE_NAME, IDX_STATUS_DUE):
        op.create_index(IDX_STATUS_DUE, TABLE_NAME, ["status", "due_at"], unique=False)
    if not _has_index(inspector, TABLE_NAME, IDX_OWNER_CREATED):
        op.create_index(
            IDX_OWNER_CREATED,
            TABLE_NAME,
            ["owner_user_id", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _has_table(inspector, TABLE_NAME):
        return

    for index_name in (
        IDX_OWNER_CREATED,
        IDX_STATUS_DUE,
        IDX_STATUS,
        IDX_DUE_AT,
        IDX_OWNER_USER_ID,
    ):
        inspector = sa.inspect(bind)
        if _has_index(inspector, TABLE_NAME, index_name):
            op.drop_index(index_name, table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
