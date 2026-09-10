import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import Date, cast, func, select
from sqlalchemy.orm import Session

from strandarr import queue
from strandarr.models.base import Observation, SourcedObservation
from strandarr.models.current_observation import CurrentObservation
from strandarr.models.vessel_position import VesselPosition
from strandarr.models.wind_observation import WindObservation

logger = logging.getLogger(__name__)

BBOX = (-6.0, 42.0, 9.5, 51.5)

GRID_STEP_DEG = 0.25
DEFAULT_BACKFILL_DAYS = 14

SOURCE_FISHING = "gfw_fishing"
SOURCE_AIS_LIVE = "ais_live"


@dataclass(frozen=True)
class SourceSpec:
    """One ingest source and the window it can actually answer for.

    Every source is clamped to its own coverage rather than to a project-wide floor, so a
    request outside it is trimmed instead of silently returning nothing -- and asking for
    strandings back to 1934 does not get pulled forward to where the forcing data starts.

    model/source drive per-day gap detection: with a model set, days already stored are not
    re-requested. Strandings leave it None because a day with no stranding is normal and
    indistinguishable from a day never fetched, and because upstream revises records.
    """

    kind: str
    first_day: date
    model: type[Observation] | None = None
    source: str | None = None
    lag_days: int = 0
    last_day: date | None = None

    def window(self, start: date, end: date) -> tuple[date, date] | None:
        available = date.today() - timedelta(days=self.lag_days)
        first = max(start, self.first_day)
        last = min(end, available, self.last_day or date.max)
        return (first, last) if first <= last else None


SOURCES = (
    SourceSpec(
        kind="ingest_vessel_positions",
        first_day=date(2012, 1, 1),
        model=VesselPosition,
        source=SOURCE_FISHING,
        lag_days=4,
    ),
    SourceSpec(
        kind="ingest_currents",
        first_day=date(2022, 1, 1),
        model=CurrentObservation,
    ),
    SourceSpec(
        kind="ingest_wind",
        first_day=date(2022, 1, 1),
        model=WindObservation,
    ),
    SourceSpec(
        kind="ingest_strandings",
        first_day=date(1934, 1, 1),
        last_day=date(2022, 12, 31),
    ),
    SourceSpec(
        kind="ingest_strandings_histocarto",
        first_day=date(2023, 1, 1),
    ),
)


def grid_points() -> list[tuple[float, float]]:
    min_lon, min_lat, max_lon, max_lat = BBOX
    points = []
    lat = min_lat
    while lat <= max_lat:
        lon = min_lon
        while lon <= max_lon:
            points.append((round(lat, 3), round(lon, 3)))
            lon += GRID_STEP_DEG
        lat += GRID_STEP_DEG
    return points


def stored_days(
    session: Session,
    model: type[Observation],
    start: date,
    end: date,
    source: str | None,
) -> set[date]:
    day = cast(func.timezone("UTC", model.recorded_at), Date)
    stmt = (
        select(day)
        .where(model.recorded_at >= start, model.recorded_at < end + timedelta(days=1))
        .distinct()
    )
    if source is not None:
        if not issubclass(model, SourcedObservation):
            raise TypeError(f"{model.__name__} has no source column to filter on")
        stmt = stmt.where(model.source == source)
    return set(session.execute(stmt).scalars())


def _missing_ranges(
    start: date, end: date, stored: set[date]
) -> list[tuple[date, date]]:
    ranges: list[tuple[date, date]] = []
    day = start
    while day <= end:
        if day in stored:
            day += timedelta(days=1)
            continue
        run_start = day
        while day <= end and day not in stored:
            day += timedelta(days=1)
        ranges.append((run_start, day - timedelta(days=1)))
    return ranges


def _ranges_for(
    session: Session, spec: SourceSpec, start: date, end: date, force: bool
) -> list[tuple[date, date]]:
    if force or spec.model is None:
        return [(start, end)]
    stored = stored_days(session, spec.model, start, end, spec.source)
    ranges = _missing_ranges(start, end, stored)
    skipped = (end - start).days + 1 - sum((r[1] - r[0]).days + 1 for r in ranges)
    if skipped:
        logger.info("%s: %d day(s) already stored, skipped", spec.kind, skipped)
    return ranges


def schedule_missing(
    session: Session,
    start: date | None = None,
    end: date | None = None,
    force: bool = False,
) -> int:
    window_end = end or date.today()
    window_start = start or window_end - timedelta(days=DEFAULT_BACKFILL_DAYS)
    if window_start > window_end:
        raise ValueError(f"start {window_start} is after end {window_end}")

    count = 0
    for spec in SOURCES:
        window = spec.window(window_start, window_end)
        if window is None:
            logger.info(
                "%s: nothing to request, %s..%s is outside its coverage",
                spec.kind,
                window_start,
                window_end,
            )
            continue
        source_start, source_end = window
        if (source_start, source_end) != (window_start, window_end):
            logger.info(
                "%s: clamped to its coverage, %s..%s",
                spec.kind,
                source_start,
                source_end,
            )
        for range_start, range_end in _ranges_for(
            session, spec, source_start, source_end, force
        ):
            queue.enqueue(
                session,
                spec.kind,
                {
                    "start": range_start.isoformat(),
                    "end": range_end.isoformat(),
                    "force": force,
                },
            )
            count += 1

    session.commit()
    logger.info("enqueued %d jobs for %s..%s", count, window_start, window_end)
    return count
