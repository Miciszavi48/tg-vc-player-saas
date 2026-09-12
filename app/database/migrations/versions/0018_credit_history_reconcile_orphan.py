"""Drop empty orphan credit_history_partitioned table (Phase 2C-1).

Revision ID: 0018_credit_hist_orphan
Revises: 0017_safe_hot_path_indexes
Create Date: 2026-06-05

Removes the unused ``credit_history_partitioned`` tree created by migration 0007.
Application code writes only to ``credit_history``. Does not alter the canonical
``credit_history`` table or its ``credit_history_default`` partition.

Production rollback should use backup/restore, not this downgrade.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0018_credit_hist_orphan"
down_revision = "0017_safe_hot_path_indexes"
branch_labels = None
depends_on = None


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgresql():
        return

    conn = op.get_bind()
    exists = conn.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = 'credit_history_partitioned'
            )
            """
        )
    ).scalar()

    if not exists:
        return

    row_count = conn.execute(
        text("SELECT COUNT(*) FROM credit_history_partitioned")
    ).scalar()

    if row_count and int(row_count) > 0:
        raise RuntimeError(
            "credit_history_partitioned contains "
            f"{int(row_count)} row(s). Phase 2C-1 does not merge data automatically. "
            "Create a manual merge plan, move rows into credit_history, then re-run "
            "alembic upgrade head."
        )

    op.execute(text("DROP TABLE IF EXISTS credit_history_partitioned CASCADE"))


def downgrade() -> None:
    """Best-effort restore empty 0007 orphan structure for dev only.

    Do not rely on this in production; restore from pg_dump instead.
    """
    if not _is_postgresql():
        return

    conn = op.get_bind()
    exists = conn.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = 'credit_history_partitioned'
            )
            """
        )
    ).scalar()

    if exists:
        return

    # Recreate empty partitioned parent compatible with historical 0007 layout.
    op.execute(
        text(
            """
            CREATE TABLE credit_history_partitioned (
                id SERIAL,
                chat_id BIGINT NOT NULL,
                chat_type VARCHAR(16) NOT NULL DEFAULT 'group',
                operation VARCHAR(32) NOT NULL,
                amount_days INTEGER NOT NULL,
                operated_by BIGINT,
                operated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                note TEXT,
                invoice_id INTEGER,
                PRIMARY KEY (id, operated_at)
            ) PARTITION BY RANGE (operated_at)
            """
        )
    )

    for month_sql in (
        """
        CREATE TABLE credit_history_y2026m01
        PARTITION OF credit_history_partitioned
        FOR VALUES FROM ('2026-01-01') TO ('2026-02-01')
        """,
        """
        CREATE TABLE credit_history_y2026m02
        PARTITION OF credit_history_partitioned
        FOR VALUES FROM ('2026-02-01') TO ('2026-03-01')
        """,
        """
        CREATE TABLE credit_history_y2026m03
        PARTITION OF credit_history_partitioned
        FOR VALUES FROM ('2026-03-01') TO ('2026-04-01')
        """,
        """
        CREATE TABLE credit_history_y2026m04
        PARTITION OF credit_history_partitioned
        FOR VALUES FROM ('2026-04-01') TO ('2026-05-01')
        """,
        """
        CREATE TABLE credit_history_y2026m05
        PARTITION OF credit_history_partitioned
        FOR VALUES FROM ('2026-05-01') TO ('2026-06-01')
        """,
    ):
        op.execute(text(month_sql))

    op.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_chp_chat_id
            ON credit_history_partitioned (chat_id)
            """
        )
    )
    op.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_chp_operated_at
            ON credit_history_partitioned (operated_at)
            """
        )
    )
    op.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_chp_invoice_id
            ON credit_history_partitioned (invoice_id)
            """
        )
    )
