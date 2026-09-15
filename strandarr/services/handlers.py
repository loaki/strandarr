import logging
from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Integer, case, delete, func, or_, select
from sqlalchemy.orm import Session

from strandarr import kinds, sources
from strandarr.analysis import drift
from strandarr.config import settings
from strandarr.connectors import gbif, gfw, open_meteo, pelagis_histocarto
from strandarr.geo import Point
from strandarr.grid import BBOX, cell, grid_points
from strandarr.models import (
    CoastalSegment,
    DriftArrival,
    DriftRelease,
    GridCell,
    MarineCondition,
    Stranding,
    VesselPosition,
)
from strandarr.repositories import store
from strandarr.services import coverage

logger = logging.getLogger(__name__)

FORCING_BATCH_ROWS = 100_000

Handler = Callable[[Session, str, dict[str, Any]], int]


def window(payload: dict[str, Any]) -> tuple[date, date]:
    return date.fromisoformat(payload["start"]), date.fromisoformat(payload["end"])


def requested_points(session: Session) -> list[Point]:
    stored = session.execute(
        select(GridCell.lat, GridCell.lon)
        .where(
            GridCell.distance_to_coast_km.isnot(None),
            GridCell.distance_to_coast_km <= settings.max_distance_to_coast_km,
            or_(
                GridCell.elevation_m < 0,
                GridCell.distance_to_coast_km <= settings.coastal_land_margin_km,
            ),
        )
        .order_by(GridCell.lat, GridCell.lon)
    ).all()
    if not stored:
        raise RuntimeError(
            "no grid cells stored: run `strandarr reference` before ingesting"
        )
    return [cell(lat, lon) for lat, lon in stored]


def _ingest_span(
    session: Session,
    kind: str,
    product: open_meteo.Product,
    points: list[Point],
    start: date,
    end: date,
    force: bool,
) -> int:
    written = 0
    counts: Counter[date] = Counter()
    for rows in open_meteo.iter_archive(product, points, start, end):
        written += store.upsert(session, MarineCondition, rows, overwrite=force)
        counts.update(row.valid_at.date() for row in rows)
    coverage.record(
        session, kind, {day: counts[day] for day in coverage.days(start, end)}
    )
    session.commit()
    return written


def archive(product: open_meteo.Product) -> Handler:
    def handler(session: Session, kind: str, payload: dict[str, Any]) -> int:
        start, end = window(payload)
        force = payload.get("force", False)
        covered = set() if force else coverage.covered_days(session, kind, start, end)
        ranges = coverage.missing_ranges(start, end, covered)
        if not ranges:
            logger.info("%s: %s..%s is already covered", product.source, start, end)
            return 0

        points = requested_points(session)
        logger.info(
            "%s: requesting %d of %d grid cells, %d day(s) already covered",
            product.source,
            len(points),
            len(grid_points()),
            len(covered),
        )
        return sum(
            _ingest_span(session, kind, product, points, first, last, force)
            for range_start, range_end in ranges
            for first, last in open_meteo.date_chunks(range_start, range_end)
        )

    return handler


def forecast(session: Session, kind: str, payload: dict[str, Any]) -> int:
    points = requested_points(session)
    total = 0
    for product in open_meteo.FORECASTS:
        hours = settings.marine_forecast_hours
        for rows in open_meteo.iter_forecast(product, points, hours):
            total += store.upsert(session, MarineCondition, rows, overwrite=True)
            session.commit()
    return total


def vessel_positions(session: Session, kind: str, payload: dict[str, Any]) -> int:
    start, end = window(payload)
    force = payload.get("force", False)
    covered = set() if force else coverage.covered_days(session, kind, start, end)
    if covered:
        logger.info(
            "%s: %d day(s) already covered, not re-requested",
            sources.GFW_FISHING,
            len(covered),
        )

    total = 0
    for day, positions in gfw.iter_positions(
        gfw.FISHING_DATASET, sources.GFW_FISHING, BBOX, start, end, covered
    ):
        written = store.upsert(session, VesselPosition, positions, overwrite=True)
        coverage.record(session, kind, {day: written})
        session.commit()
        total += written
    return total


def strandings(
    fetch: Callable[[tuple[float, float, float, float], date, date], list[Stranding]],
) -> Handler:
    def handler(session: Session, kind: str, payload: dict[str, Any]) -> int:
        start, end = window(payload)
        rows = fetch(BBOX, start, end)
        written = store.upsert(
            session, Stranding, rows, overwrite=payload.get("force", False)
        )
        session.commit()
        return written

    return handler


strandings_gbif = strandings(gbif.fetch_strandings)
strandings_histocarto = strandings(pelagis_histocarto.fetch_strandings)


def _segments(session: Session) -> list[CoastalSegment]:
    rows = list(
        session.execute(select(CoastalSegment).order_by(CoastalSegment.id)).scalars()
    )
    if not rows:
        raise RuntimeError(
            "no coastal segments stored: run `strandarr reference` before drifting"
        )
    return rows


def _forcing(session: Session, day: date) -> drift.Forcing:
    start, end = drift.window(day)
    hour = func.floor(
        func.extract("epoch", MarineCondition.valid_at - start) / 3600
    ).cast(Integer)
    precedence = case(
        {source: rank for rank, source in enumerate(sources.OBSERVED_BEFORE_PREDICTED)},
        value=MarineCondition.source,
        else_=len(sources.OBSERVED_BEFORE_PREDICTED),
    )
    statement = select(
        hour,
        precedence,
        MarineCondition.lat,
        MarineCondition.lon,
        *(getattr(MarineCondition, name) for name in drift.MEASUREMENTS),
    ).where(MarineCondition.valid_at >= start, MarineCondition.valid_at < end)
    return drift.build_forcing(
        session.execute(
            statement.execution_options(yield_per=FORCING_BATCH_ROWS)
        ).partitions()
    )


def _effort(session: Session, day: date) -> list[VesselPosition]:
    start = datetime.combine(day, time.min, tzinfo=UTC)
    return list(
        session.execute(
            select(VesselPosition).where(
                VesselPosition.recorded_at >= start,
                VesselPosition.recorded_at < start + timedelta(days=1),
            )
        ).scalars()
    )


def drift_arrivals(session: Session, kind: str, payload: dict[str, Any]) -> int:
    start, end = window(payload)
    force = payload.get("force", False)
    done = set() if force else coverage.covered_days(session, kind, start, end)
    effort = coverage.covered_days(session, kinds.VESSEL_POSITIONS, start, end)
    todo = [day for day in coverage.days(start, end) if day not in done]
    pending = [day for day in todo if day in effort]
    if done:
        logger.info("drift: %d day(s) already complete, skipped", len(done))
    if len(pending) < len(todo):
        logger.info(
            "drift: %d day(s) waiting on %s, not simulated",
            len(todo) - len(pending),
            kinds.VESSEL_POSITIONS,
        )
    if not pending:
        return 0
    coast = drift.build_coast(_segments(session))

    total = 0
    for day in pending:
        released_at = datetime.combine(day, time.min, tzinfo=UTC)
        seeds = drift.seeds(_effort(session, day), day)
        result = drift.simulate(seeds, _forcing(session, day), coast)

        session.execute(
            delete(DriftArrival).where(
                DriftArrival.release_at == released_at,
                DriftArrival.model_version == drift.MODEL_VERSION,
            )
        )
        store.upsert(
            session,
            DriftArrival,
            [
                DriftArrival(
                    release_at=released_at,
                    model_version=drift.MODEL_VERSION,
                    coastal_segment_id=arrival.segment_id,
                    arrival_at=released_at + timedelta(hours=arrival.hour),
                    expected_count=arrival.expected_count,
                )
                for arrival in result.arrivals
            ],
        )
        store.upsert(
            session,
            DriftRelease,
            [
                DriftRelease(
                    release_at=released_at,
                    model_version=drift.MODEL_VERSION,
                    complete=result.complete,
                )
            ],
            overwrite=True,
        )
        coverage.record_day(session, kind, day, len(result.arrivals), result.complete)
        session.commit()

        logger.info(
            "drift %s: %d seed(s), %d particle(s), %.3f released -> %.3f stranded "
            "on %d segment-hour(s), %dh of forcing%s",
            day,
            len(seeds),
            result.particles,
            result.released_weight,
            result.stranded_weight,
            len(result.arrivals),
            result.forcing_hours,
            "" if result.complete else " (incomplete, will re-run)",
        )
        total += len(result.arrivals)
    return total
