"""Initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-25 14:51:30.982990

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cell",
        sa.Column("id", sa.SmallInteger(), autoincrement=True, nullable=False),
        sa.Column("lat", sa.REAL(), nullable=False),
        sa.Column("lon", sa.REAL(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cell_position", "cell", ["lat", "lon"], unique=True)
    op.create_table(
        "coastal_segment",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
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
        "job",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "running", "done", "failed", name="jobstatus"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("deferrals", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_claim", "job", ["status", "not_before"], unique=False)
    op.create_index("ix_job_dedup", "job", ["kind", "day"], unique=True)
    op.create_table(
        "stranding",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("species_scientific", sa.String(length=255), nullable=True),
        sa.Column("species_common", sa.String(length=255), nullable=True),
        sa.Column("individual_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_stranding_dedup", "stranding", ["source", "external_id"], unique=True
    )
    op.create_index(
        op.f("ix_stranding_recorded_at"), "stranding", ["recorded_at"], unique=False
    )
    op.create_table(
        "vessel",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("mmsi", sa.String(length=16), nullable=False),
        sa.Column("ship_name", sa.String(length=128), nullable=True),
        sa.Column("flag", sa.String(length=8), nullable=True),
        sa.Column("gear_type", sa.String(length=64), nullable=True),
        sa.Column("vessel_type", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vessel_mmsi", "vessel", ["mmsi"], unique=True)
    op.create_table(
        "drift_daily",
        sa.Column("release_day", sa.Date(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("coastal_segment_id", sa.Integer(), nullable=False),
        sa.Column("drift_index", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["coastal_segment_id"], ["coastal_segment.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("release_day", "day", "coastal_segment_id"),
    )
    op.create_index("ix_drift_daily_day", "drift_daily", ["day"], unique=False)
    op.create_table(
        "sea",
        sa.Column("valid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cell_id", sa.SmallInteger(), nullable=False),
        sa.Column("forecast", sa.Boolean(), nullable=False),
        sa.Column("current_speed_kmh", sa.REAL(), nullable=True),
        sa.Column("current_direction_deg", sa.REAL(), nullable=True),
        sa.Column("wave_height_m", sa.REAL(), nullable=True),
        sa.Column("wave_direction_deg", sa.REAL(), nullable=True),
        sa.Column("wave_period_s", sa.REAL(), nullable=True),
        sa.Column("swell_height_m", sa.REAL(), nullable=True),
        sa.Column("swell_direction_deg", sa.REAL(), nullable=True),
        sa.Column("swell_period_s", sa.REAL(), nullable=True),
        sa.ForeignKeyConstraint(["cell_id"], ["cell.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("valid_at", "cell_id"),
    )
    op.create_table(
        "segment_risk",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("coastal_segment_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
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
        sa.PrimaryKeyConstraint("day", "coastal_segment_id"),
    )
    op.create_table(
        "vessel_position",
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("vessel_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("lat", sa.REAL(), nullable=False),
        sa.Column("lon", sa.REAL(), nullable=False),
        sa.Column("effort_hours", sa.REAL(), nullable=True),
        sa.ForeignKeyConstraint(["vessel_id"], ["vessel.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("recorded_at", "vessel_id"),
    )
    op.create_table(
        "wind",
        sa.Column("valid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cell_id", sa.SmallInteger(), nullable=False),
        sa.Column("forecast", sa.Boolean(), nullable=False),
        sa.Column("speed_kmh", sa.REAL(), nullable=False),
        sa.Column("direction_deg", sa.REAL(), nullable=False),
        sa.ForeignKeyConstraint(["cell_id"], ["cell.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("valid_at", "cell_id"),
    )


def downgrade() -> None:
    op.drop_table("wind")
    op.drop_table("vessel_position")
    op.drop_table("segment_risk")
    op.drop_table("sea")
    op.drop_index("ix_drift_daily_day", table_name="drift_daily")
    op.drop_table("drift_daily")
    op.drop_index("ix_vessel_mmsi", table_name="vessel")
    op.drop_table("vessel")
    op.drop_index(op.f("ix_stranding_recorded_at"), table_name="stranding")
    op.drop_index("ix_stranding_dedup", table_name="stranding")
    op.drop_table("stranding")
    op.drop_index("ix_job_dedup", table_name="job")
    op.drop_index("ix_job_claim", table_name="job")
    op.drop_table("job")
    op.drop_index("ix_coastal_segment_external_id", table_name="coastal_segment")
    op.drop_table("coastal_segment")
    op.drop_index("ix_cell_position", table_name="cell")
    op.drop_table("cell")
