"""Measure ingested days instead of asserting they are complete.

Carried over unchanged. The deployed database stopped one revision short of
this, so it still has to run there before the refactor can.

Revision ID: 6a3c72fb1e94
Revises: 5f2b94d0e3a8
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "6a3c72fb1e94"
down_revision: str | None = "5f2b94d0e3a8"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ingest_coverage",
        sa.Column("cell_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ingest_coverage",
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column("ingest_coverage", "cell_count", server_default=None)


def downgrade() -> None:
    op.drop_column("ingest_coverage", "checked_at")
    op.drop_column("ingest_coverage", "cell_count")
