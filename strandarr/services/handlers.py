import logging
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Integer, case, delete, func, or_, select
from sqlalchemy.orm import Session

from strandarr import sources
from strandarr.analysis import drift
from strandarr.config import settings
from strandarr.connectors import gbif, gfw, open_meteo, pelagis_histocarto
from strandarr.geo import Point
from strandarr.grid import BBOX, cell, grid_points
from strandarr.models import (
    Base,
    CoastalSegment,
    DriftArrival,
    DriftRelease,
    GridCell,
    MarineCondition,
    Stranding,
    VesselPosition,
)
from strandarr.repositories import store
from strandarr.services.coverage import stored_days

logger = logging.getLogger(__name__)

FORCING_BATCH_ROWS = 100_000

Handler = Callable[[Session, dict[str, Any]], int]


def window(payload: dict[str, Any]) -> tuple[date, date]:
    return date.fromisoformat(payload["start"]), date.fromisoformat(payload["end"])


def write[ModelT: Base](
    session: Session,
    model: type[ModelT],
    batches: Iterable[Sequence[ModelT]],
    overwrite: bool,
) -> int:
    total = 0
    for rows in batches:
        total += store.upsert(session, model, rows, overwrite=overwrite)
        session.commit()
    return total


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


def archive(product: open_meteo.Product) -> Handler:
    def handler(session: Session, payload: dict[str, Any]) -> int:
        start, end = window(payload)
        points = requested_points(session)
        logger.info(
            "%s: requesting %d of %d grid cells",
            product.source,
            len(points),
            len(grid_points()),
        )
        return write(
            session,
            MarineCondition,
            open_meteo.iter_archive(product, points, start, end),
            overwrite=payload.get("force", False),
        )

    return handler


def forecast(session: Session, payload: dict[str, Any]) -> int:
    points = requested_points(session)
    total = 0
    for product in open_meteo.FORECASTS:
        total += write(
            session,
            MarineCondition,
            open_meteo.iter_forecast(product, points, settings.marine_forecast_hours),
            overwrite=True,
        )
    return total


def vessel_positions(session: Session, payload: dict[str, Any]) -> int:
    start, end = window(payload)
    skip = (
        set()
        if payload.get("force", False)
        else stored_days(
            session,
            VesselPosition.recorded_at,
            start,
            end,
            (VesselPosition.source == sources.GFW_FISHING,),
        )
    )
    if skip:
        logger.info(
            "%s: %d day(s) already stored, not re-requested",
            sources.GFW_FISHING,
            len(skip),
        )
    return write(
        session,
        VesselPosition,
        gfw.iter_positions(
            gfw.FISHING_DATASET, sources.GFW_FISHING, BBOX, start, end, skip
        ),
        overwrite=True,
    )


def strandings(
    fetch: Callable[[tuple[float, float, float, float], date, date], list[Stranding]],
) -> Handler:
    def handler(session: Session, payload: dict[str, Any]) -> int:
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


def drift_arrivals(session: Session, payload: dict[str, Any]) -> int:
    start, end = window(payload)
    done = (
        set()
        if payload.get("force", False)
        else stored_days(
            session,
            DriftRelease.release_at,
            start,
            end,
            (
                DriftRelease.complete.is_(True),
                DriftRelease.model_version == drift.MODEL_VERSION,
            ),
        )
    )
    if done:
        logger.info("drift: %d day(s) already complete, skipped", len(done))
    coast = drift.build_coast(_segments(session))

    total = 0
    day = start
    while day <= end:
        if day in done:
            day += timedelta(days=1)
            continue
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
        day += timedelta(days=1)
    return total
