"""collapse drift arrivals to daily totals, add the 48h forecast"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "4e1a83c9d2f7"
down_revision: Union[str, Sequence[str], None] = "3d9e62b4c15a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "drift_daily",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("release_day", sa.Date(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("coastal_segment_id", sa.Integer(), nullable=False),
        sa.Column("model_version", sa.String(length=16), nullable=False),
        sa.Column("drift_index", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["coastal_segment_id"], ["coastal_segment.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_drift_daily_dedup",
        "drift_daily",
        ["release_day", "day", "coastal_segment_id", "model_version"],
        unique=True,
    )
    op.create_index("ix_drift_daily_day", "drift_daily", ["day"])

    op.create_table(
        "segment_forecast",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("coastal_segment_id", sa.Integer(), nullable=False),
        sa.Column("model_version", sa.String(length=16), nullable=False),
        sa.Column("probability", sa.Float(), nullable=False),
        sa.Column("persistence", sa.Float(), nullable=False),
        sa.Column("drift_index", sa.Float(), nullable=False),
        sa.Column("swell_m", sa.Float(), nullable=False),
        sa.Column("onshore_m", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["coastal_segment_id"], ["coastal_segment.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_segment_forecast_dedup",
        "segment_forecast",
        ["day", "coastal_segment_id", "model_version"],
        unique=True,
    )
    op.create_index("ix_segment_forecast_day", "segment_forecast", ["day"])

    bind = op.get_bind()
    if sa.inspect(bind).has_table("drift_arrival"):
        op.execute(
            """
            insert into drift_daily (created_at, release_day, day,
                coastal_segment_id, model_version, drift_index)
            select now(), (release_at at time zone 'UTC')::date,
                   (arrival_at at time zone 'UTC')::date,
                   coastal_segment_id, model_version, sum(drift_index)
            from drift_arrival
            group by 2, 3, 4, 5
            """
        )


def downgrade() -> None:
    op.drop_index("ix_segment_forecast_day", table_name="segment_forecast")
    op.drop_index("ix_segment_forecast_dedup", table_name="segment_forecast")
    op.drop_table("segment_forecast")
    op.drop_index("ix_drift_daily_day", table_name="drift_daily")
    op.drop_index("ix_drift_daily_dedup", table_name="drift_daily")
    op.drop_table("drift_daily")
