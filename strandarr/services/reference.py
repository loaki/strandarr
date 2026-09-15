import logging
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.orm import Session

from strandarr.config import settings
from strandarr.connectors import bathymetry, coastline
from strandarr.geo import NearestIndex, Point
from strandarr.grid import grid_points
from strandarr.models import CoastalSegment, GridCell
from strandarr.repositories import store

logger = logging.getLogger(__name__)


def segments(session: Session) -> list[CoastalSegment]:
    rows = list(
        session.execute(select(CoastalSegment).order_by(CoastalSegment.id)).scalars()
    )
    if not rows:
        raise RuntimeError(
            "no coastal segments stored: run `strandarr reference` first"
        )
    return rows


def build(session: Session) -> dict[str, int]:
    lines = coastline.fetch_lines()
    built = coastline.segments(lines)
    written = {
        "coastal_segments": store.upsert(session, CoastalSegment, built, overwrite=True)
    }
    written["stale_segments_removed"] = _prune_segments(session, built)
    session.commit()
    written["grid_cells"] = _grid_cells(session, coastline.nearest_index(lines))
    return written


def _prune_segments(session: Session, current: list[CoastalSegment]) -> int:
    keep = {segment.external_id for segment in current}
    result = cast(
        "CursorResult[Any]",
        session.execute(
            delete(CoastalSegment).where(CoastalSegment.external_id.notin_(keep))
        ),
    )
    removed = result.rowcount
    if removed:
        logger.info("coastline: removed %d segment(s) no longer on the coast", removed)
    return int(removed)


def _grid_cells(session: Session, coast: NearestIndex) -> int:
    limit = settings.max_distance_to_coast_km
    distances: dict[Point, float] = {}
    for point in grid_points():
        distance = coast.distance_km(point, limit)
        if distance is not None:
            distances[point] = distance
    logger.info(
        "grid: %d of %d cells within %.0f km of shore",
        len(distances),
        len(grid_points()),
        limit,
    )
    elevations = bathymetry.fetch_elevations(list(distances))
    rows = [
        GridCell(
            lat=point[0],
            lon=point[1],
            elevation_m=elevation,
            distance_to_coast_km=distances[point],
        )
        for point, elevation in elevations.items()
    ]
    written = store.upsert(session, GridCell, rows, overwrite=True)
    session.commit()
    logger.info(
        "grid: %d cell(s) stored, %d of them sea",
        written,
        sum(1 for row in rows if row.elevation_m < 0),
    )
    return written
