"""Bind each PostgreSQL database to one bot instance.

Revision ID: 0033_instance_database_ownership
Revises: 0032_fast_creat_token_pool
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0033_instance_database_ownership"
down_revision = "0032_fast_creat_token_pool"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_instance_metadata",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("instance_id", sa.String(length=32), nullable=False),
        sa.Column(
            "claimed_at",
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
            "id = 1",
            name="ck_bot_instance_metadata_singleton",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "instance_id",
            name="uq_bot_instance_metadata_instance_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("bot_instance_metadata")
