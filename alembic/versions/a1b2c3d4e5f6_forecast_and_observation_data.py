"""add marine forecasts, stranding uncertainty, and observation coverage"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "9c2f5a81b7e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stranding", sa.Column("coordinate_uncertainty_m", sa.Float()))
    op.add_column("stranding", sa.Column("time_uncertainty_hours", sa.Float()))
    op.add_column("stranding", sa.Column("location_precision", sa.String(32)))
    op.add_column("stranding", sa.Column("is_live", sa.Boolean()))

    op.create_table(
        "marine_forecast",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("wind_speed_kmh", sa.Float()),
        sa.Column("wind_direction_deg", sa.Float()),
        sa.Column("current_speed_kmh", sa.Float()),
        sa.Column("current_direction_deg", sa.Float()),
        sa.Column("wave_height_m", sa.Float()),
        sa.Column("wave_direction_deg", sa.Float()),
        sa.Column("wave_period_s", sa.Float()),
        sa.Column("swell_height_m", sa.Float()),
        sa.Column("swell_direction_deg", sa.Float()),
        sa.Column("swell_period_s", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_marine_forecast_dedup",
        "marine_forecast",
        ["retrieved_at", "valid_at", "lat", "lon"],
        unique=True,
    )
    op.create_index(
        "ix_marine_forecast_retrieved_at", "marine_forecast", ["retrieved_at"]
    )
    op.create_index("ix_marine_forecast_valid_at", "marine_forecast", ["valid_at"])

    op.create_table(
        "wave_observation",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("wave_height_m", sa.Float(), nullable=False),
        sa.Column("wave_direction_deg", sa.Float(), nullable=False),
        sa.Column("wave_period_s", sa.Float(), nullable=False),
        sa.Column("swell_height_m", sa.Float()),
        sa.Column("swell_direction_deg", sa.Float()),
        sa.Column("swell_period_s", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_wave_observation_dedup",
        "wave_observation",
        ["recorded_at", "lat", "lon", "source"],
        unique=True,
    )
    op.create_index(
        "ix_wave_observation_recorded_at",
        "wave_observation",
        ["recorded_at"],
    )

    op.create_table(
        "coastal_segment",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.Column("center_lat", sa.Float(), nullable=False),
        sa.Column("center_lon", sa.Float(), nullable=False),
        sa.Column("length_km", sa.Float(), nullable=False),
        sa.Column("beach_length_km", sa.Float()),
        sa.Column("coastline_orientation_deg", sa.Float()),
        sa.Column("accessibility_score", sa.Float()),
        sa.Column("habitat_type", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_coastal_segment_external_id",
        "coastal_segment",
        ["external_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_wave_observation_recorded_at", table_name="wave_observation")
    op.drop_index("ix_wave_observation_dedup", table_name="wave_observation")
    op.drop_table("wave_observation")
    op.drop_index("ix_coastal_segment_external_id", table_name="coastal_segment")
    op.drop_table("coastal_segment")
    op.drop_index("ix_marine_forecast_valid_at", table_name="marine_forecast")
    op.drop_index("ix_marine_forecast_retrieved_at", table_name="marine_forecast")
    op.drop_index("ix_marine_forecast_dedup", table_name="marine_forecast")
    op.drop_table("marine_forecast")
    op.drop_column("stranding", "is_live")
    op.drop_column("stranding", "location_precision")
    op.drop_column("stranding", "time_uncertainty_hours")
    op.drop_column("stranding", "coordinate_uncertainty_m")
