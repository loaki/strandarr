"""track ingested days in ingest_coverage"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7e2b9c4a1d50"
down_revision: Union[str, Sequence[str], None] = "3c81de4a09b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BACKFILL = (
    """
    INSERT INTO ingest_coverage (kind, day, row_count, complete, created_at)
    SELECT 'ingest_vessel_positions',
           (timezone('UTC', recorded_at))::date,
           count(*),
           true,
           now()
      FROM vessel_position
     WHERE source = 'gfw_fishing'
     GROUP BY 2
        ON CONFLICT (kind, day) DO NOTHING
    """,
    """
    INSERT INTO ingest_coverage (kind, day, row_count, complete, created_at)
    SELECT 'ingest_weather_archive',
           (timezone('UTC', valid_at))::date,
           count(*),
           true,
           now()
      FROM marine_condition
     WHERE source = 'weather_archive'
     GROUP BY 2
        ON CONFLICT (kind, day) DO NOTHING
    """,
    """
    INSERT INTO ingest_coverage (kind, day, row_count, complete, created_at)
    SELECT 'ingest_marine_archive',
           (timezone('UTC', valid_at))::date,
           count(*),
           true,
           now()
      FROM marine_condition
     WHERE source = 'marine_archive'
     GROUP BY 2
        ON CONFLICT (kind, day) DO NOTHING
    """,
    """
    INSERT INTO ingest_coverage (kind, day, row_count, complete, created_at)
    SELECT 'compute_drift_arrivals',
           (timezone('UTC', r.release_at))::date,
           count(a.id),
           true,
           now()
      FROM drift_release r
      LEFT JOIN drift_arrival a
             ON a.release_at = r.release_at
            AND a.model_version = r.model_version
     WHERE r.complete
       AND r.model_version = 'v1'
     GROUP BY 2
        ON CONFLICT (kind, day) DO NOTHING
    """,
)


def upgrade() -> None:
    op.create_table(
        "ingest_coverage",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ingest_coverage_dedup", "ingest_coverage", ["kind", "day"], unique=True
    )
    for statement in BACKFILL:
        op.execute(statement)


def downgrade() -> None:
    op.drop_index("ix_ingest_coverage_dedup", table_name="ingest_coverage")
    op.drop_table("ingest_coverage")
