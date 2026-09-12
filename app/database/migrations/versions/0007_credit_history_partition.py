"""Partition credit_history table by month for scale.

Converts the existing credit_history table to range-partitioned by operated_at.
Creates partitions for the current and next 3 months, plus a default partition.
A scheduled job should create future partitions monthly.
"""

from alembic import op

revision = "0007_credit_history_partition"
down_revision = "0006_helper_extensions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS credit_history_partitioned (
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
        ) PARTITION BY RANGE (operated_at);
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS credit_history_default
        PARTITION OF credit_history_partitioned DEFAULT;
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS credit_history_y2026m01
        PARTITION OF credit_history_partitioned
        FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS credit_history_y2026m02
        PARTITION OF credit_history_partitioned
        FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS credit_history_y2026m03
        PARTITION OF credit_history_partitioned
        FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS credit_history_y2026m04
        PARTITION OF credit_history_partitioned
        FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS credit_history_y2026m05
        PARTITION OF credit_history_partitioned
        FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
    """)

    op.execute("""
        INSERT INTO credit_history_partitioned
            (chat_id, chat_type, operation, amount_days, operated_by, operated_at, note, invoice_id)
        SELECT chat_id, chat_type, operation, amount_days, operated_by, operated_at, note, invoice_id
        FROM credit_history
        ON CONFLICT DO NOTHING;
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_chp_chat_id ON credit_history_partitioned (chat_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chp_operated_at ON credit_history_partitioned (operated_at);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chp_invoice_id ON credit_history_partitioned (invoice_id);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS credit_history_partitioned CASCADE;")
