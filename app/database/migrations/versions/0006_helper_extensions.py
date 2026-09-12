"""Extend helper_accounts/bindings + add helper_events audit log."""

from alembic import op
import sqlalchemy as sa

revision = "0006_helper_extensions"
down_revision = "0005_analytics_hourly"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("helper_accounts",
                  sa.Column("tg_user_id", sa.BigInteger, nullable=True, unique=True))
    op.add_column("helper_accounts",
                  sa.Column("display_name", sa.String(255), nullable=True))
    op.add_column("helper_accounts",
                  sa.Column("username", sa.String(128), nullable=True))
    op.add_column("helper_accounts",
                  sa.Column("quarantine_count", sa.Integer, nullable=False, server_default="0"))

    op.add_column("helper_chat_bindings",
                  sa.Column("bound_by", sa.BigInteger, nullable=True))
    op.add_column("helper_chat_bindings",
                  sa.Column("binding_state", sa.String(32), nullable=False, server_default="bound"))
    op.add_column("helper_chat_bindings",
                  sa.Column("last_error", sa.Text, nullable=True))

    op.create_table(
        "helper_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False, server_default="cli"),
        sa.Column("helper_account_id", sa.Integer, nullable=True),
        sa.Column("chat_id", sa.BigInteger, nullable=True),
        sa.Column("metadata_json", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_helper_events_type", "helper_events", ["event_type"])
    op.create_index("ix_helper_events_helper", "helper_events", ["helper_account_id"])
    op.create_index("ix_helper_events_created", "helper_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("helper_events")
    for col in ("last_error", "binding_state", "bound_by"):
        op.drop_column("helper_chat_bindings", col)
    for col in ("quarantine_count", "username", "display_name", "tg_user_id"):
        op.drop_column("helper_accounts", col)
