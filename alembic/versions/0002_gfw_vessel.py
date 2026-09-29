"""Remember which vessel a Global Fishing Watch vesselId resolved to

Revision ID: 0002_gfw_vessel
Revises: 0001_initial
Create Date: 2026-09-29 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_gfw_vessel"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("vessel", "mmsi", existing_type=sa.String(length=16), nullable=True)
    op.create_table(
        "gfw_vessel",
        sa.Column("gfw_id", sa.String(length=64), nullable=False),
        sa.Column("vessel_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["vessel_id"], ["vessel.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("gfw_id"),
    )
    op.create_index("ix_gfw_vessel_vessel_id", "gfw_vessel", ["vessel_id"])


def downgrade() -> None:
    op.drop_index("ix_gfw_vessel_vessel_id", table_name="gfw_vessel")
    op.drop_table("gfw_vessel")
    op.execute("DELETE FROM vessel WHERE mmsi IS NULL")
    op.alter_column(
        "vessel", "mmsi", existing_type=sa.String(length=16), nullable=False
    )
