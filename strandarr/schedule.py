import logging
from datetime import date, timedelta

from sqlalchemy import Date, cast, func, select
from sqlalchemy.orm import Session

from strandarr import queue
from strandarr.models.current_observation import CurrentObservation
from strandarr.models.vessel_position import VesselPosition
from strandarr.models.wind_observation import WindObservation

logger = logging.getLogger(__name__)

BBOX = (-6.0, 42.0, 9.5, 51.5)
# Both upstream models resolve to ~0.07-0.08 deg (~8-9 km), measured by probing which
# coordinates they snap requests to; a finer grid than that returns duplicated values.
GRID_STEP_DEG = 0.25
DEFAULT_BACKFILL_DAYS = 14

# Open-Meteo's marine model (currents) has no data before this date; it is the binding
# constraint, so every source is clamped to it to keep the series aligned.
MIN_DATE = date(2022, 1, 1)

SOURCE_FISHING = "gfw_fishing"

# GFW publishes with a few days' delay; requesting newer days returns 0 rows, and gap
# detection would otherwise re-request them on every run and exhaust the API rate limit.
GFW_LAG_DAYS = 5

# Currents are enqueued before wind: they define which grid cells are sea, and wind
# uses that to skip inland cells.
WINDOW_SOURCES = (
    ("ingest_vessel_positions", VesselPosition, SOURCE_FISHING, GFW_LAG_DAYS),
    ("ingest_currents", CurrentObservation, None, 0),
    ("ingest_wind", WindObservation, None, 0),
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
    session: Session, model: type, start: date, end: date, source: str | None
) -> set[date]:
    day = cast(func.timezone("UTC", model.recorded_at), Date)
    stmt = (
        select(day)
        .where(model.recorded_at >= start, model.recorded_at < end + timedelta(days=1))
        .distinct()
    )
    if source is not None:
        stmt = stmt.where(model.source == source)
    return set(session.execute(stmt).scalars())


def _missing_ranges(start: date, end: date, stored: set[date]) -> list[tuple[date, date]]:
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


def schedule_missing(
    session: Session,
    start: date | None = None,
    end: date | None = None,
    force: bool = False,
) -> int:
    window_end = end or date.today()
    window_start = start or window_end - timedelta(days=DEFAULT_BACKFILL_DAYS)
    if window_start < MIN_DATE:
        logger.info("clamping start %s to %s (earliest date all sources cover)", window_start, MIN_DATE)
        window_start = MIN_DATE
    if window_start > window_end:
        raise ValueError(f"start {window_start} is after end {window_end}")

    count = 0
    for kind, model, source, lag in WINDOW_SOURCES:
        source_end = min(window_end, date.today() - timedelta(days=lag))
        if source_end < window_start:
            logger.info("%s: nothing to request, upstream lags %d day(s)", kind, lag)
            continue
        if force:
            ranges = [(window_start, source_end)]
        else:
            stored = stored_days(session, model, window_start, source_end, source)
            ranges = _missing_ranges(window_start, source_end, stored)
            skipped = (source_end - window_start).days + 1 - sum(
                (r[1] - r[0]).days + 1 for r in ranges
            )
            if skipped:
                logger.info("%s: %d day(s) already stored, skipped", kind, skipped)
        for range_start, range_end in ranges:
            queue.enqueue(
                session,
                kind,
                {
                    "start": range_start.isoformat(),
                    "end": range_end.isoformat(),
                    "force": force,
                },
            )
            count += 1

    for kind in ("ingest_strandings", "ingest_strandings_histocarto"):
        queue.enqueue(
            session,
            kind,
            {
                "start": window_start.isoformat(),
                "end": window_end.isoformat(),
                "force": force,
            },
        )
        count += 1

    session.commit()
    logger.info("enqueued %d jobs for %s..%s", count, window_start, window_end)
    return count
