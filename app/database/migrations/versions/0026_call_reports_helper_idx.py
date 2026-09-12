"""Add call_reports helper account FK index.

Revision ID: 0026_call_reports_helper_idx
Revises: 0025_group_member_membership
Create Date: 2026-06-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026_call_reports_helper_idx"
down_revision = "0025_group_member_membership"
branch_labels = None
depends_on = None

INDEX_NAME = "idx_call_reports_helper_account_id"
TABLE_NAME = "call_reports"


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    if table_name not in inspector.get_table_names():
        return False
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _has_index(inspector, TABLE_NAME, INDEX_NAME):
        op.create_index(
            INDEX_NAME,
            TABLE_NAME,
            ["helper_account_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _has_index(inspector, TABLE_NAME, INDEX_NAME):
        op.drop_index(INDEX_NAME, table_name=TABLE_NAME)
