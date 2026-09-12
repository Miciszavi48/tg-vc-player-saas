"""Add global_bans table for developer ban-all users."""

from alembic import op
import sqlalchemy as sa

revision = "0016_global_bans"
down_revision = "0015_admin_titles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "global_bans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(op.f("ix_global_bans_user_id"), "global_bans", ["user_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_global_bans_user_id"), table_name="global_bans")
    op.drop_table("global_bans")
