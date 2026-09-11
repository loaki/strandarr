"""add sea surface temperature, tide height and the bathymetric grid"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4f8a2b19d63"
down_revision: Union[str, Sequence[str], None] = "b7d3e1f04c28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "marine_condition", sa.Column("sea_surface_temperature_c", sa.Float())
    )
    op.add_column("marine_condition", sa.Column("sea_level_m", sa.Float()))

    op.create_table(
        "grid_cell",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("elevation_m", sa.Float(), nullable=False),
        sa.Column("distance_to_coast_km", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_grid_cell_dedup", "grid_cell", ["lat", "lon"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_grid_cell_dedup", table_name="grid_cell")
    op.drop_table("grid_cell")
    op.drop_column("marine_condition", "sea_level_m")
    op.drop_column("marine_condition", "sea_surface_temperature_c")
