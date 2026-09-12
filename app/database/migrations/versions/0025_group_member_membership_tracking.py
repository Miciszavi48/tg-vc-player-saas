"""Add group membership tracking for Call Security.

Revision ID: 0025_group_member_membership
Revises: 0024_call_security_settings
Create Date: 2026-06-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025_group_member_membership"
down_revision = "0024_call_security_settings"
branch_labels = None
depends_on = None


def _has_column(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    if table_name not in inspector.get_table_names():
        return False
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if not _has_column(inspector, "call_security_settings", "membership_age_days"):
        op.add_column(
            "call_security_settings",
            sa.Column(
                "membership_age_days",
                sa.Integer(),
                server_default=sa.text("7"),
                nullable=False,
            ),
        )

    if "group_member_memberships" not in tables:
        op.create_table(
            "group_member_memberships",
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "source",
                sa.String(length=64),
                server_default=sa.text("'manual_unknown'"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("chat_id", "user_id", name="pk_group_member_memberships"),
        )
        op.create_index(
            "ix_group_member_memberships_chat_id",
            "group_member_memberships",
            ["chat_id"],
        )
        op.create_index(
            "ix_group_member_memberships_user_id",
            "group_member_memberships",
            ["user_id"],
        )
        op.create_index(
            "ix_group_member_memberships_joined_at",
            "group_member_memberships",
            ["joined_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "group_member_memberships" in inspector.get_table_names():
        op.drop_table("group_member_memberships")
    if _has_column(inspector, "call_security_settings", "membership_age_days"):
        op.drop_column("call_security_settings", "membership_age_days")
