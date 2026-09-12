"""Add last_daily_deducted_on for midnight credit deduction idempotency.

Revision ID: 0019_daily_deduct_idem
Revises: 0018_credit_hist_orphan
Create Date: 2026-06-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_daily_deduct_idem"
down_revision = "0018_credit_hist_orphan"
branch_labels = None
depends_on = None

_INDEX_NAME = "idx_group_credits_daily_deduct_due"

_UPGRADE_INDEX_SQL = f"""
CREATE INDEX CONCURRENTLY IF NOT EXISTS {_INDEX_NAME}
ON group_credits (last_daily_deducted_on, id)
WHERE status = 'active' AND credit_days > 0
"""


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.add_column(
        "group_credits",
        sa.Column("last_daily_deducted_on", sa.Date(), nullable=True),
    )

    if not _is_postgresql():
        return

    with op.get_context().autocommit_block():
        op.execute(_UPGRADE_INDEX_SQL)


def downgrade() -> None:
    if _is_postgresql():
        with op.get_context().autocommit_block():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}")

    op.drop_column("group_credits", "last_daily_deducted_on")
