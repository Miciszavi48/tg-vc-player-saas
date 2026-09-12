"""Add indexes for admin report credit/no-credit/renewal queries."""

from alembic import op

revision = "0010_admin_report_indexes"
down_revision = "0009_helper_fingerprint_proxy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_group_credits_status_type_days_id",
        "group_credits",
        ["status", "chat_type", "credit_days", "id"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_group_credits_type_days_id",
        "group_credits",
        ["chat_type", "credit_days", "id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    for idx in (
        "idx_group_credits_status_type_days_id",
        "idx_group_credits_type_days_id",
    ):
        try:
            op.drop_index(idx)
        except Exception:
            pass
