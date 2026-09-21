"""Merge forecast, drift and climatology into one segment_risk.

Carried over unchanged. It creates segment_risk empty and drops
segment_forecast and segment_climatology; 0004 then drops segment_risk in turn,
because the refactored model needs two signals none of these tables ever held.
Everything on that path is recomputed locally, without an API call.

Revision ID: 7b4d18e6c052
Revises: 6a3c72fb1e94
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7b4d18e6c052"
down_revision: str | None = "6a3c72fb1e94"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

OLD_KIND = "compute_forecast_zones"
NEW_KIND = "compute_segment_risk"
CLIMATOLOGY_KIND = "compute_climatology"


def upgrade() -> None:
    op.create_table(
        "segment_risk",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("coastal_segment_id", sa.Integer(), nullable=False),
        sa.Column("model_version", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("probability", sa.Float(), nullable=False),
        sa.Column("seasonal", sa.Float(), nullable=False),
        sa.Column("drift_index", sa.Float(), nullable=False),
        sa.Column("persistence", sa.Float(), nullable=False),
        sa.Column("swell_m", sa.Float(), nullable=False),
        sa.Column("onshore_m", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["coastal_segment_id"], ["coastal_segment.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_segment_risk_dedup",
        "segment_risk",
        ["day", "coastal_segment_id", "model_version"],
        unique=True,
    )
    op.create_index("ix_segment_risk_day", "segment_risk", ["day"])

    op.execute(
        sa.text("DELETE FROM ingest_coverage WHERE kind = :new_kind").bindparams(
            new_kind=NEW_KIND
        )
    )
    op.execute(
        sa.text(
            "UPDATE ingest_coverage SET kind = :new_kind WHERE kind = :old_kind"
        ).bindparams(new_kind=NEW_KIND, old_kind=OLD_KIND)
    )
    op.execute(
        sa.text("DELETE FROM ingest_coverage WHERE kind = :kind").bindparams(
            kind=CLIMATOLOGY_KIND
        )
    )
    op.execute(
        sa.text(
            "UPDATE job SET kind = :new_kind "
            "WHERE kind = :old_kind AND status IN ('pending', 'running')"
        ).bindparams(new_kind=NEW_KIND, old_kind=OLD_KIND)
    )
    op.execute(
        sa.text(
            "DELETE FROM job WHERE kind = :kind AND status IN ('pending', 'running')"
        ).bindparams(kind=CLIMATOLOGY_KIND)
    )

    op.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
    op.drop_table("segment_forecast")
    op.drop_table("segment_climatology")


def downgrade() -> None:
    op.create_table(
        "segment_climatology",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("coastal_segment_id", sa.Integer(), nullable=False),
        sa.Column("day_of_year", sa.Integer(), nullable=False),
        sa.Column("model_version", sa.String(length=16), nullable=False),
        sa.Column("observed", sa.Float(), nullable=False),
        sa.Column("expected_per_day", sa.Float(), nullable=False),
        sa.Column("probability", sa.Float(), nullable=False),
        sa.Column("years", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["coastal_segment_id"], ["coastal_segment.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_segment_climatology_dedup",
        "segment_climatology",
        ["coastal_segment_id", "day_of_year", "model_version"],
        unique=True,
    )
    op.create_index(
        "ix_segment_climatology_day", "segment_climatology", ["day_of_year"]
    )

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

    op.execute(
        sa.text(
            "UPDATE ingest_coverage SET kind = :old_kind WHERE kind = :new_kind"
        ).bindparams(new_kind=NEW_KIND, old_kind=OLD_KIND)
    )
    op.execute(
        sa.text(
            "UPDATE job SET kind = :old_kind "
            "WHERE kind = :new_kind AND status IN ('pending', 'running')"
        ).bindparams(new_kind=NEW_KIND, old_kind=OLD_KIND)
    )

    op.drop_index("ix_segment_risk_day", table_name="segment_risk")
    op.drop_index("ix_segment_risk_dedup", table_name="segment_risk")
    op.drop_table("segment_risk")
