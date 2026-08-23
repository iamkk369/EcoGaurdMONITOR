"""Add violations.event_id for violation persistence deduplication (Step 1).

Revision: 0002
Down revision: 0001
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_violations_event_id_unique"


def upgrade() -> None:
    """Add nullable event_id + PostgreSQL partial unique index (non-null only)."""
    op.add_column(
        "violations",
        sa.Column("event_id", sa.String(64), nullable=True),
    )
    op.create_index(
        INDEX_NAME,
        "violations",
        ["event_id"],
        unique=True,
        postgresql_where=sa.text("event_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Remove the index and column."""
    op.drop_index(INDEX_NAME, table_name="violations")
    op.drop_column("violations", "event_id")