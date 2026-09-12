"""Add device fingerprint and proxy columns to helper_accounts."""

from alembic import op
import sqlalchemy as sa

revision = "0009_helper_fingerprint_proxy"
down_revision = "0008_broadcast_scheduling"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("helper_accounts", sa.Column("device_model", sa.String(128), nullable=True))
    op.add_column("helper_accounts", sa.Column("system_version", sa.String(64), nullable=True))
    op.add_column("helper_accounts", sa.Column("app_version", sa.String(64), nullable=True))
    op.add_column("helper_accounts", sa.Column("lang_code", sa.String(8), nullable=True))
    op.add_column("helper_accounts", sa.Column("proxy_type", sa.String(16), nullable=True))
    op.add_column("helper_accounts", sa.Column("proxy_host", sa.String(255), nullable=True))
    op.add_column("helper_accounts", sa.Column("proxy_port", sa.Integer, nullable=True))
    op.add_column("helper_accounts", sa.Column("proxy_username", sa.String(128), nullable=True))
    op.add_column("helper_accounts", sa.Column("proxy_password", sa.String(128), nullable=True))


def downgrade() -> None:
    for col in ("proxy_password", "proxy_username", "proxy_port",
                "proxy_host", "proxy_type", "lang_code",
                "app_version", "system_version", "device_model"):
        op.drop_column("helper_accounts", col)
