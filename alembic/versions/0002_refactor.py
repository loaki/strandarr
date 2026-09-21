"""Fold the schema onto the refactored models, keeping the data.

What survives, and how:

  marine_condition -> condition   four source rows per cell-hour collapse into
                                  one, taking the archived value where there is
                                  one and the forecast otherwise. That is what
                                  the old read path did on every query, so no
                                  value changes meaning.
  drift_daily                     kept. The drift physics did not change, so the
                                  stored arrivals stay valid; only model_version
                                  goes, after keeping the newest row per key.
  coastal_segment                 kept, column renamed. external_id is unchanged,
                                  so segment ids -- and every row pointing at
                                  them -- survive.
  stranding, vessel_position      kept untouched.
  job                             rebuilt on (kind, day), then seeded `done` from
                                  ingest_coverage so the refactored scheduler does
                                  not re-download years of history.
  segment_risk                    dropped. The new model needs wave_m and
                                  period_s, which were never stored; filling them
                                  with zeros would corrupt `strandarr fit`, which
                                  reads this table directly. It is recomputed
                                  locally, with no API calls.
  grid_cell, ingest_coverage,     dropped. Reference and bookkeeping data that
  drift_release                   the refactored code derives or replaces.

Run scripts/export-grid.sh BEFORE this migration: it copies the grid grid_cell
describes into strandarr/grid_points.json, which is the only thing in that table
the new code still needs.

Revision ID: 0002_refactor
Revises: 7b4d18e6c052
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_refactor"
down_revision: str | None = "7b4d18e6c052"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

TIMESTAMP = sa.DateTime(timezone=True)

# Bucket lattice and chunk sizes must match strandarr/jobs.py, or a seeded job
# would claim a day range the scheduler never asks for.
CHUNK_EPOCH = "2020-01-06"
ARCHIVE_CHUNK_DAYS = 14

WIND = ("wind_speed_kmh", "wind_direction_deg")
SEA = (
    "current_speed_kmh",
    "current_direction_deg",
    "wave_height_m",
    "wave_direction_deg",
    "wave_period_s",
    "swell_height_m",
    "swell_direction_deg",
    "swell_period_s",
    "sea_surface_temperature_c",
    "sea_level_m",
)
MEASUREMENTS = WIND + SEA

# Where each measurement comes from, archived first and forecast second. Wind is
# the weather product, everything else the marine one.
ORIGINS: dict[str, tuple[str, str]] = {
    **dict.fromkeys(WIND, ("weather_archive", "weather_forecast")),
    **dict.fromkeys(SEA, ("marine_archive", "marine_forecast")),
}
FORECAST_SOURCES = ("weather_forecast", "marine_forecast")

# ingest_coverage counted per day; the refactored scheduler counts per bucket.
PER_DAY_KINDS = {"ingest_vessel_positions": "gfw", "compute_drift_arrivals": "drift"}
CHUNKED_KINDS = {
    "ingest_weather_archive": "weather_archive",
    "ingest_marine_archive": "marine_archive",
}


def _common() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False),
    ]


def _merged_column(name: str) -> str:
    """Archived value first, forecast second -- the old read-time precedence.

    (valid_at, lat, lon, source) was unique, so each FILTER matches at most one
    row: max() picks that row's value rather than combining anything.
    """
    archive, forecast = ORIGINS[name]
    return (
        f"coalesce("
        f"max({name}) FILTER (WHERE source = '{archive}'), "
        f"max({name}) FILTER (WHERE source = '{forecast}'))"
    )


def upgrade() -> None:
    op.alter_column(
        "coastal_segment",
        "coastline_orientation_deg",
        new_column_name="orientation_deg",
    )

    _merge_conditions()
    _collapse_drift_daily()
    _rebuild_segment_risk()
    op.drop_column("stranding", "coordinate_precision_deg")
    _rebuild_job()

    op.drop_table("ingest_coverage")
    op.drop_table("drift_release")
    op.drop_table("grid_cell")


def _merge_conditions() -> None:
    op.create_table(
        "condition",
        *_common(),
        sa.Column("valid_at", TIMESTAMP, nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("forecast", sa.Boolean(), nullable=False),
        *(sa.Column(name, sa.Float(), nullable=True) for name in MEASUREMENTS),
        sa.PrimaryKeyConstraint("id"),
    )

    forecast_sources = ", ".join(f"'{name}'" for name in FORECAST_SOURCES)
    merged = ",\n               ".join(_merged_column(name) for name in MEASUREMENTS)
    columns = ", ".join(MEASUREMENTS)
    # One pass, indexes afterwards: building them as the rows land would cost
    # far more, and there is nothing to enforce until the table is whole.
    op.execute(
        f"""
        INSERT INTO condition (created_at, valid_at, lat, lon, forecast, {columns})
        SELECT min(created_at),
               valid_at,
               lat,
               lon,
               bool_and(source IN ({forecast_sources})),
               {merged}
        FROM marine_condition
        GROUP BY valid_at, lat, lon
        """
    )
    op.create_index("ix_condition_valid_at", "condition", ["valid_at"])
    op.create_index("ix_condition_cell", "condition", ["lat", "lon"])
    op.create_index(
        "ix_condition_dedup", "condition", ["valid_at", "lat", "lon"], unique=True
    )
    op.drop_table("marine_condition")


def _collapse_drift_daily() -> None:
    # The key loses model_version, so anything that differed only by it collides.
    # Keep the newest, by version then by insertion order.
    op.execute(
        """
        DELETE FROM drift_daily older
        USING drift_daily newer
        WHERE older.release_day = newer.release_day
          AND older.day = newer.day
          AND older.coastal_segment_id = newer.coastal_segment_id
          AND (older.model_version, older.id) < (newer.model_version, newer.id)
        """
    )
    op.drop_index("ix_drift_daily_dedup", table_name="drift_daily")
    op.drop_column("drift_daily", "model_version")
    op.create_index(
        "ix_drift_daily_dedup",
        "drift_daily",
        ["release_day", "day", "coastal_segment_id"],
        unique=True,
    )


def _rebuild_segment_risk() -> None:
    op.drop_table("segment_risk")
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


def _rebuild_job() -> None:
    # Queue state is transient, so it is rebuilt rather than migrated. The
    # jobstatus type outlives the table it belonged to, hence create_type=False.
    op.drop_table("job")
    op.create_table(
        "job",
        *_common(),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending",
                "running",
                "done",
                "failed",
                name="jobstatus",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("deferrals", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("not_before", TIMESTAMP, nullable=True),
        sa.Column("started_at", TIMESTAMP, nullable=True),
        sa.Column("finished_at", TIMESTAMP, nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_dedup", "job", ["kind", "day"], unique=True)
    op.create_index("ix_job_claim", "job", ["status", "not_before"])
    _seed_job()


def _seed_job() -> None:
    """Carry the ingest history over, so nothing already fetched is fetched again.

    Not seeded: `risk` (dropped above, so every day has to be recomputed),
    `forecast` (rolling), and the two stranding kinds (never tracked in
    ingest_coverage, and cheap to request again).
    """
    for old, new in PER_DAY_KINDS.items():
        op.execute(
            f"""
            INSERT INTO job (created_at, kind, day, status, attempts, deferrals)
            SELECT now(), '{new}', day, 'done', 1, 0
            FROM ingest_coverage
            WHERE kind = '{old}' AND complete
            ON CONFLICT DO NOTHING
            """
        )

    for old, new in CHUNKED_KINDS.items():
        # A bucket counts as done only when all of its days are. ingest_coverage
        # is unique on (kind, day), so a full count means all present and complete.
        # Buckets clipped by a product's first day never reach it and are simply
        # requested again -- at most one chunk per kind.
        op.execute(
            f"""
            INSERT INTO job (created_at, kind, day, status, attempts, deferrals)
            SELECT now(), '{new}', bucket, 'done', 1, 0
            FROM (
                SELECT DATE '{CHUNK_EPOCH}' + (
                           FLOOR((day - DATE '{CHUNK_EPOCH}')::numeric
                                 / {ARCHIVE_CHUNK_DAYS}) * {ARCHIVE_CHUNK_DAYS}
                       )::int AS bucket,
                       complete
                FROM ingest_coverage
                WHERE kind = '{old}'
            ) AS days
            GROUP BY bucket
            HAVING count(*) FILTER (WHERE complete) = {ARCHIVE_CHUNK_DAYS}
            ON CONFLICT DO NOTHING
            """
        )


def downgrade() -> None:
    raise NotImplementedError(
        "This migration merges four condition sources into one row and drops "
        "segment_risk, so it cannot be undone from what is left. Restore the dump "
        "taken before the upgrade instead (scripts/restore-db.sh)."
    )
