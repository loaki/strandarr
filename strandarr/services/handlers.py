import logging
from collections.abc import Callable, Iterable, Sequence
from datetime import date
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from strandarr import sources
from strandarr.config import settings
from strandarr.connectors import gbif, gfw, open_meteo, pelagis_histocarto
from strandarr.geo import Point
from strandarr.grid import BBOX, cell, grid_points
from strandarr.models import Base, GridCell, MarineCondition, Stranding, VesselPosition
from strandarr.repositories import store
from strandarr.services.coverage import stored_days

logger = logging.getLogger(__name__)

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
