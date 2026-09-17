from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

TIMESTAMP = sa.DateTime(timezone=True)


def _common() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "coastal_segment",
        *_common(),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.Column("center_lat", sa.Float(), nullable=False),
        sa.Column("center_lon", sa.Float(), nullable=False),
        sa.Column("length_km", sa.Float(), nullable=False),
        sa.Column("orientation_deg", sa.Float(), nullable=True),
        sa.Column("path", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_coastal_segment_external_id",
        "coastal_segment",
        ["external_id"],
        unique=True,
    )

    op.create_table(
        "condition",
        *_common(),
        sa.Column("valid_at", TIMESTAMP, nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("forecast", sa.Boolean(), nullable=False),
        sa.Column("wind_speed_kmh", sa.Float(), nullable=True),
        sa.Column("wind_direction_deg", sa.Float(), nullable=True),
        sa.Column("current_speed_kmh", sa.Float(), nullable=True),
        sa.Column("current_direction_deg", sa.Float(), nullable=True),
        sa.Column("wave_height_m", sa.Float(), nullable=True),
        sa.Column("wave_direction_deg", sa.Float(), nullable=True),
        sa.Column("wave_period_s", sa.Float(), nullable=True),
        sa.Column("swell_height_m", sa.Float(), nullable=True),
        sa.Column("swell_direction_deg", sa.Float(), nullable=True),
        sa.Column("swell_period_s", sa.Float(), nullable=True),
        sa.Column("sea_surface_temperature_c", sa.Float(), nullable=True),
        sa.Column("sea_level_m", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_condition_valid_at", "condition", ["valid_at"])
    op.create_index("ix_condition_cell", "condition", ["lat", "lon"])
    op.create_index(
        "ix_condition_dedup", "condition", ["valid_at", "lat", "lon"], unique=True
    )

    op.create_table(
        "vessel_position",
        *_common(),
        sa.Column("recorded_at", TIMESTAMP, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("mmsi", sa.String(16), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("ship_name", sa.String(128), nullable=True),
        sa.Column("flag", sa.String(8), nullable=True),
        sa.Column("gear_type", sa.String(64), nullable=True),
        sa.Column("vessel_type", sa.String(64), nullable=True),
        sa.Column("effort_hours", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_vessel_position_recorded_at", "vessel_position", ["recorded_at"]
    )
    op.create_index("ix_vessel_position_mmsi", "vessel_position", ["mmsi"])
    op.create_index(
        "ix_vessel_position_dedup",
        "vessel_position",
        ["mmsi", "recorded_at"],
        unique=True,
    )

    op.create_table(
        "stranding",
        *_common(),
        sa.Column("recorded_at", TIMESTAMP, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("species_scientific", sa.String(255), nullable=True),
        sa.Column("species_common", sa.String(255), nullable=True),
        sa.Column("individual_count", sa.Integer(), nullable=False),
        sa.Column("coordinate_uncertainty_m", sa.Float(), nullable=True),
        sa.Column("time_uncertainty_hours", sa.Float(), nullable=True),
        sa.Column("location_precision", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stranding_recorded_at", "stranding", ["recorded_at"])
    op.create_index(
        "ix_stranding_dedup", "stranding", ["source", "external_id"], unique=True
    )

    op.create_table(
        "drift_daily",
        *_common(),
        sa.Column("release_day", sa.Date(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("coastal_segment_id", sa.Integer(), nullable=False),
        sa.Column("drift_index", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["coastal_segment_id"], ["coastal_segment.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_drift_daily_day", "drift_daily", ["day"])
    op.create_index(
        "ix_drift_daily_dedup",
        "drift_daily",
        ["release_day", "day", "coastal_segment_id"],
        unique=True,
    )

    op.create_table(
        "segment_risk",
        *_common(),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("coastal_segment_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("probability", sa.Float(), nullable=False),
        sa.Column("seasonal", sa.Float(), nullable=False),
        sa.Column("persistence", sa.Float(), nullable=False),
        sa.Column("drift_index", sa.Float(), nullable=False),
        sa.Column("swell_m", sa.Float(), nullable=False),
        sa.Column("wave_m", sa.Float(), nullable=False),
        sa.Column("onshore_m", sa.Float(), nullable=False),
        sa.Column("period_s", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["coastal_segment_id"], ["coastal_segment.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_segment_risk_day", "segment_risk", ["day"])
    op.create_index(
        "ix_segment_risk_dedup",
        "segment_risk",
        ["day", "coastal_segment_id"],
        unique=True,
    )

    op.create_table(
        "job",
        *_common(),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "running", "done", "failed", name="jobstatus"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("not_before", TIMESTAMP, nullable=True),
        sa.Column("started_at", TIMESTAMP, nullable=True),
        sa.Column("finished_at", TIMESTAMP, nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_dedup", "job", ["kind", "day"], unique=True)
    op.create_index("ix_job_claim", "job", ["status", "not_before"])


def downgrade() -> None:
    for table in (
        "job",
        "segment_risk",
        "drift_daily",
        "stranding",
        "vessel_position",
        "condition",
        "coastal_segment",
    ):
        op.drop_table(table)
    sa.Enum(name="jobstatus").drop(op.get_bind(), checkfirst=True)
