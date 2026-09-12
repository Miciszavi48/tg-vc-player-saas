"""Add analytics_hourly table."""

from alembic import op
import sqlalchemy as sa

revision = "0005_analytics_hourly"
down_revision = "0004_fm_broadcast"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analytics_hourly",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("hour", sa.SmallInteger, nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("scope_key", sa.String(128), nullable=False),
        sa.Column("metric", sa.String(128), nullable=False),
        sa.Column("value", sa.BigInteger, nullable=False, server_default="0"),
    )
    op.create_unique_constraint(
        "uq_analytics_hourly_composite", "analytics_hourly",
        ["day", "hour", "scope", "scope_key", "metric"],
    )
    op.create_index("ix_analytics_day_scope", "analytics_hourly", ["day", "scope", "scope_key"])
    op.create_index("ix_analytics_day_metric", "analytics_hourly", ["day", "metric"])
    op.create_index("ix_analytics_day_hour_metric", "analytics_hourly", ["day", "hour", "metric"])


def downgrade() -> None:
    op.drop_table("analytics_hourly")
