"""vessel_position dedup on the vessel-hour alone, not per source"""

from typing import Sequence, Union

from alembic import op

revision: str = "9c2f5a81b7e4"
down_revision: Union[str, Sequence[str], None] = "8b1c47d29fa3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DELETE FROM vessel_position v
        USING (
            SELECT mmsi,
                   recorded_at,
                   (array_agg(id ORDER BY (source = 'ais_live'), id))[1] AS keep
            FROM vessel_position
            GROUP BY mmsi, recorded_at
            HAVING count(*) > 1
        ) d
        WHERE v.mmsi = d.mmsi
          AND v.recorded_at = d.recorded_at
          AND v.id <> d.keep
    """)
    op.drop_index("ix_vessel_position_dedup", table_name="vessel_position")
    op.create_index(
        "ix_vessel_position_dedup",
        "vessel_position",
        ["mmsi", "recorded_at"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_vessel_position_dedup", table_name="vessel_position")
    op.create_index(
        "ix_vessel_position_dedup",
        "vessel_position",
        ["mmsi", "recorded_at", "source"],
        unique=True,
    )
