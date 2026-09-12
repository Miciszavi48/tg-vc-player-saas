"""Add scheduling columns to broadcasts table."""

from alembic import op
import sqlalchemy as sa

revision = "0008_broadcast_scheduling"
down_revision = "0007_credit_history_partition"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("broadcasts", sa.Column("run_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("broadcasts", sa.Column("interval_hours", sa.Integer, nullable=True))
    op.add_column("broadcasts", sa.Column("target_types_json", sa.Text, nullable=True))
    op.add_column("broadcasts", sa.Column("filter_type", sa.String(32), nullable=True))
    op.add_column("broadcasts", sa.Column("source_admin_chat_id", sa.BigInteger, nullable=True))
    op.add_column("broadcasts", sa.Column("source_admin_msg_id", sa.BigInteger, nullable=True))


def downgrade() -> None:
    for col in ("source_admin_msg_id", "source_admin_chat_id", "filter_type",
                "target_types_json", "interval_hours", "run_at"):
        op.drop_column("broadcasts", col)
