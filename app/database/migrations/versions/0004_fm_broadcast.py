"""Extend force_join_channels and add broadcasts table."""

from alembic import op
import sqlalchemy as sa

revision = "0004_fm_broadcast"
down_revision = "0003_credit_history_partition"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("force_join_channels",
                  sa.Column("display_name", sa.String(255), nullable=True))
    op.add_column("force_join_channels",
                  sa.Column("chat_type", sa.String(16), nullable=False, server_default="channel"))
    op.add_column("force_join_channels",
                  sa.Column("position", sa.Integer, nullable=False, server_default="0"))
    op.add_column("force_join_channels",
                  sa.Column("verify_status", sa.String(32), nullable=False, server_default="pending"))
    op.add_column("force_join_channels",
                  sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("force_join_channels",
                  sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("force_join_channels",
                  sa.Column("last_error", sa.Text, nullable=True))
    op.add_column("force_join_channels",
                  sa.Column("updated_at", sa.DateTime(timezone=True),
                            server_default=sa.func.now(), nullable=False))
    op.create_index("ix_fjc_position", "force_join_channels", ["position"])

    op.create_table(
        "broadcasts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("admin_id", sa.BigInteger, nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("target_scope", sa.String(16), nullable=False),
        sa.Column("payload_type", sa.String(32), nullable=True),
        sa.Column("text_content", sa.Text, nullable=True),
        sa.Column("entities_json", sa.Text, nullable=True),
        sa.Column("caption", sa.Text, nullable=True),
        sa.Column("caption_entities_json", sa.Text, nullable=True),
        sa.Column("file_id", sa.String(512), nullable=True),
        sa.Column("source_chat_id", sa.BigInteger, nullable=True),
        sa.Column("source_message_id", sa.BigInteger, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("total_recipients", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sent_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("fail_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_broadcasts_admin_id", "broadcasts", ["admin_id"])
    op.create_index("ix_broadcasts_status", "broadcasts", ["status"])
    op.create_index("ix_broadcasts_created_at", "broadcasts", ["created_at"])


def downgrade() -> None:
    op.drop_table("broadcasts")
    op.drop_index("ix_fjc_position", table_name="force_join_channels")
    for col in ("updated_at", "last_error", "last_checked_at", "verified_at",
                "verify_status", "position", "chat_type", "display_name"):
        op.drop_column("force_join_channels", col)
