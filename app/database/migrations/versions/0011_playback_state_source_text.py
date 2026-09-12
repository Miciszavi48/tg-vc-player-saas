"""Store long playback source URLs.

Revision ID: 0011_playback_state_source_text
Revises: 0010_admin_report_indexes
Create Date: 2026-05-29
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_playback_state_source_text"
down_revision = "0010_admin_report_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "playback_states",
        "source",
        existing_type=sa.String(length=64),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "playback_states",
        "source",
        existing_type=sa.Text(),
        type_=sa.String(length=64),
        existing_nullable=True,
    )
