"""Prepare credit_history for range partitioning.

For **new installs** (empty table) the migration converts the table to
partitioned-by-range on ``operated_at`` and creates a default partition.

For **existing installs** with data already in the table the migration
is a safe no-op that logs a notice — manual partitioning can be done
during a maintenance window via pg_partman or manual ATTACH PARTITION.

Revision ID: 0003_credit_history_partition
Revises: 0002_spec_indexes
Create Date: 2026-02-25
"""
from alembic import op
from sqlalchemy import text

revision = "0003_credit_history_partition"
down_revision = "0002_spec_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    row_count = conn.execute(
        text("SELECT count(*) FROM credit_history")
    ).scalar()

    if row_count > 0:
        conn.execute(text(
            "DO $$ BEGIN RAISE NOTICE "
            "'credit_history has % rows — skipping auto-partition. "
            "Run manual partitioning during a maintenance window.', "
            "(SELECT count(*) FROM credit_history); END $$;"
        ))
        return

    conn.execute(text("ALTER TABLE credit_history RENAME TO credit_history_old"))
    conn.execute(text("""
        CREATE TABLE credit_history (
            id          SERIAL,
            chat_id     BIGINT NOT NULL,
            chat_type   VARCHAR(16) NOT NULL DEFAULT 'group',
            operation   VARCHAR(32) NOT NULL,
            amount_days INTEGER NOT NULL,
            operated_by BIGINT,
            operated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            note        TEXT,
            invoice_id  INTEGER,
            PRIMARY KEY (id, operated_at)
        ) PARTITION BY RANGE (operated_at)
    """))
    conn.execute(text(
        "CREATE TABLE credit_history_default PARTITION OF credit_history DEFAULT"
    ))
    conn.execute(text(
        "CREATE INDEX idx_credit_history_chat ON credit_history (chat_id)"
    ))
    conn.execute(text(
        "CREATE INDEX idx_credit_history_invoice ON credit_history (invoice_id)"
    ))
    conn.execute(text("DROP TABLE credit_history_old"))


def downgrade() -> None:
    conn = op.get_bind()
    is_partitioned = conn.execute(text(
        "SELECT relkind FROM pg_class WHERE relname = 'credit_history'"
    )).scalar()

    if is_partitioned == 'p':
        conn.execute(text(
            "CREATE TABLE credit_history_flat AS SELECT * FROM credit_history"
        ))
        conn.execute(text("DROP TABLE credit_history CASCADE"))
        conn.execute(text(
            "ALTER TABLE credit_history_flat RENAME TO credit_history"
        ))
        conn.execute(text(
            "ALTER TABLE credit_history ADD PRIMARY KEY (id)"
        ))
