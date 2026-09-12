"""Add owner_text_links table for per-owner text/link overrides.

Revision ID: 0022_owner_text_links
Revises: 0021_now_playing_flags
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_owner_text_links"
down_revision = "0021_now_playing_flags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "owner_text_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_user_id",
            "key",
            name="uq_owner_text_links_owner_key",
        ),
    )
    op.create_index(
        op.f("ix_owner_text_links_owner_user_id"),
        "owner_text_links",
        ["owner_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_owner_text_links_owner_user_id"),
        table_name="owner_text_links",
    )
    op.drop_table("owner_text_links")
