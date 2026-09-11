"""unify wind, currents, waves and forecasts into marine_condition"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7d3e1f04c28"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

WEATHER_ARCHIVE = "weather_archive"
MARINE_ARCHIVE = "marine_archive"
WEATHER_FORECAST = "weather_forecast"
MARINE_FORECAST = "marine_forecast"
WAVE_REANALYSIS = "open_meteo_marine_reanalysis"

SEA_COLUMNS = (
    "current_speed_kmh",
    "current_direction_deg",
    "wave_height_m",
    "wave_direction_deg",
    "wave_period_s",
    "swell_height_m",
    "swell_direction_deg",
    "swell_period_s",
)


def upgrade() -> None:
    op.create_table(
        "marine_condition",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("valid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
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
        "ix_marine_condition_dedup",
        "marine_condition",
        ["valid_at", "lat", "lon", "source"],
        unique=True,
    )
    op.create_index(
        "ix_marine_condition_source_cell", "marine_condition", ["source", "lat", "lon"]
    )
    op.create_index("ix_marine_condition_valid_at", "marine_condition", ["valid_at"])

    op.execute(
        f"""
        INSERT INTO marine_condition (
            valid_at, issued_at, source, lat, lon,
            wind_speed_kmh, wind_direction_deg, created_at
        )
        SELECT recorded_at, created_at, '{WEATHER_ARCHIVE}', lat, lon,
               speed, direction, created_at
        FROM wind_observation
        """
    )

    op.execute(
        f"""
        INSERT INTO marine_condition (
            valid_at, issued_at, source, lat, lon,
            current_speed_kmh, current_direction_deg,
            wave_height_m, wave_direction_deg, wave_period_s,
            swell_height_m, swell_direction_deg, swell_period_s, created_at
        )
        SELECT
            COALESCE(c.recorded_at, w.recorded_at),
            COALESCE(c.created_at, w.created_at),
            '{MARINE_ARCHIVE}',
            COALESCE(c.lat, w.lat),
            COALESCE(c.lon, w.lon),
            c.speed, c.direction,
            w.wave_height_m, w.wave_direction_deg, w.wave_period_s,
            w.swell_height_m, w.swell_direction_deg, w.swell_period_s,
            LEAST(c.created_at, w.created_at)
        FROM current_observation c
        FULL OUTER JOIN wave_observation w
          ON  c.recorded_at = w.recorded_at
          AND c.lat = w.lat
          AND c.lon = w.lon
        """
    )

    for source, columns in (
        (WEATHER_FORECAST, ("wind_speed_kmh", "wind_direction_deg")),
        (MARINE_FORECAST, SEA_COLUMNS),
    ):
        selected = ", ".join(columns)
        not_null = " OR ".join(f"{name} IS NOT NULL" for name in columns)
        op.execute(
            f"""
            INSERT INTO marine_condition (
                valid_at, issued_at, source, lat, lon, {selected}, created_at
            )
            SELECT DISTINCT ON (valid_at, lat, lon)
                   valid_at, retrieved_at, '{source}', lat, lon, {selected}, created_at
            FROM marine_forecast
            WHERE {not_null}
            ORDER BY valid_at, lat, lon, retrieved_at DESC
            """
        )

    op.drop_index("ix_wind_observation_dedup", table_name="wind_observation")
    op.drop_index("ix_wind_observation_recorded_at", table_name="wind_observation")
    op.drop_table("wind_observation")
    op.drop_index("ix_current_observation_dedup", table_name="current_observation")
    op.drop_index(
        "ix_current_observation_recorded_at", table_name="current_observation"
    )
    op.drop_table("current_observation")
    op.drop_index("ix_wave_observation_dedup", table_name="wave_observation")
    op.drop_index("ix_wave_observation_recorded_at", table_name="wave_observation")
    op.drop_table("wave_observation")
    op.drop_index("ix_marine_forecast_dedup", table_name="marine_forecast")
    op.drop_index("ix_marine_forecast_retrieved_at", table_name="marine_forecast")
    op.drop_index("ix_marine_forecast_valid_at", table_name="marine_forecast")
    op.drop_table("marine_forecast")

    op.add_column("stranding", sa.Column("coordinate_precision_deg", sa.Float()))

    op.execute(
        """
        UPDATE stranding
        SET recorded_at = recorded_at + INTERVAL '12 hours'
        WHERE time_uncertainty_hours = 12
          AND recorded_at = date_trunc('day', recorded_at)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE stranding
        SET recorded_at = recorded_at - INTERVAL '12 hours'
        WHERE time_uncertainty_hours = 12
          AND recorded_at = date_trunc('day', recorded_at) + INTERVAL '12 hours'
        """
    )
    op.drop_column("stranding", "coordinate_precision_deg")

    op.create_table(
        "wind_observation",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("speed", sa.Float(), nullable=False),
        sa.Column("direction", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_wind_observation_dedup",
        "wind_observation",
        ["recorded_at", "lat", "lon"],
        unique=True,
    )
    op.create_index(
        "ix_wind_observation_recorded_at", "wind_observation", ["recorded_at"]
    )
    op.create_table(
        "current_observation",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("speed", sa.Float(), nullable=False),
        sa.Column("direction", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_current_observation_dedup",
        "current_observation",
        ["recorded_at", "lat", "lon"],
        unique=True,
    )
    op.create_index(
        "ix_current_observation_recorded_at", "current_observation", ["recorded_at"]
    )
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
        "ix_wave_observation_recorded_at", "wave_observation", ["recorded_at"]
    )
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

    op.execute(
        f"""
        INSERT INTO wind_observation (
            recorded_at, lat, lon, speed, direction, created_at
        )
        SELECT valid_at, lat, lon, wind_speed_kmh, wind_direction_deg, created_at
        FROM marine_condition
        WHERE source = '{WEATHER_ARCHIVE}'
          AND wind_speed_kmh IS NOT NULL
          AND wind_direction_deg IS NOT NULL
        """
    )
    op.execute(
        f"""
        INSERT INTO current_observation (
            recorded_at, lat, lon, speed, direction, created_at
        )
        SELECT valid_at, lat, lon, current_speed_kmh, current_direction_deg, created_at
        FROM marine_condition
        WHERE source = '{MARINE_ARCHIVE}'
          AND current_speed_kmh IS NOT NULL
          AND current_direction_deg IS NOT NULL
        """
    )
    op.execute(
        f"""
        INSERT INTO wave_observation (
            recorded_at, lat, lon, source,
            wave_height_m, wave_direction_deg, wave_period_s,
            swell_height_m, swell_direction_deg, swell_period_s, created_at
        )
        SELECT valid_at, lat, lon, '{WAVE_REANALYSIS}',
               wave_height_m, wave_direction_deg, wave_period_s,
               swell_height_m, swell_direction_deg, swell_period_s, created_at
        FROM marine_condition
        WHERE source = '{MARINE_ARCHIVE}'
          AND wave_height_m IS NOT NULL
          AND wave_direction_deg IS NOT NULL
          AND wave_period_s IS NOT NULL
        """
    )
    op.execute(
        f"""
        INSERT INTO marine_forecast (
            retrieved_at, valid_at, lat, lon,
            wind_speed_kmh, wind_direction_deg,
            current_speed_kmh, current_direction_deg,
            wave_height_m, wave_direction_deg, wave_period_s,
            swell_height_m, swell_direction_deg, swell_period_s, created_at
        )
        SELECT
            MAX(issued_at), valid_at, lat, lon,
            MAX(wind_speed_kmh), MAX(wind_direction_deg),
            MAX(current_speed_kmh), MAX(current_direction_deg),
            MAX(wave_height_m), MAX(wave_direction_deg), MAX(wave_period_s),
            MAX(swell_height_m), MAX(swell_direction_deg), MAX(swell_period_s),
            MIN(created_at)
        FROM marine_condition
        WHERE source IN ('{WEATHER_FORECAST}', '{MARINE_FORECAST}')
        GROUP BY valid_at, lat, lon
        """
    )

    op.drop_index("ix_marine_condition_valid_at", table_name="marine_condition")
    op.drop_index("ix_marine_condition_source_cell", table_name="marine_condition")
    op.drop_index("ix_marine_condition_dedup", table_name="marine_condition")
    op.drop_table("marine_condition")
