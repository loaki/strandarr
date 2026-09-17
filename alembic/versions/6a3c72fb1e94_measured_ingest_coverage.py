"""measure ingested days instead of asserting they are complete"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "6a3c72fb1e94"
down_revision: Union[str, Sequence[str], None] = "5f2b94d0e3a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
