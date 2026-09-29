"""Allow vessels without an MMSI (GFW vessels the identity API cannot resolve)

Revision ID: 0003_vessel_mmsi_nullable
Revises: 0002_gfw_vessel
Create Date: 2026-09-29 17:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_vessel_mmsi_nullable"
down_revision: str | Sequence[str] | None = "0002_gfw_vessel"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("vessel", "mmsi", existing_type=sa.String(length=16), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM vessel WHERE mmsi IS NULL")
    op.alter_column(
        "vessel", "mmsi", existing_type=sa.String(length=16), nullable=False
    )
