import logging

from sqlalchemy.orm import Session

from strandarr.config import settings
from strandarr.connectors import bathymetry, coastline
from strandarr.db.queries.reference import prune_segments
from strandarr.db.upsert import upsert
from strandarr.geo import NearestIndex, Point
from strandarr.grid import GRID
from strandarr.models import CoastalSegment, GridCell

logger = logging.getLogger(__name__)


def build(session: Session) -> dict[str, int]:
    lines = coastline.fetch_lines()
    built = coastline.segments(lines)
    written = {
        "coastal_segments": upsert(session, CoastalSegment, built, overwrite=True),
        "stale_segments_removed": prune_segments(
            session, {segment.external_id for segment in built}
        ),
    }
    session.commit()
    written["grid_cells"] = _grid_cells(session, coastline.nearest_index(lines))
    return written


def _grid_cells(session: Session, coast: NearestIndex) -> int:
    limit = settings.max_distance_to_coast_km
    points = GRID.points()
    distances: dict[Point, float] = {}
    for point in points:
        distance = coast.distance_km(point, limit)
        if distance is not None:
            distances[point] = distance
    logger.info(
        "grid: %d of %d cells within %.0f km of shore",
        len(distances),
        len(points),
        limit,
    )
    rows = [
        GridCell(
            lat=point[0],
            lon=point[1],
            elevation_m=elevation,
            distance_to_coast_km=distances[point],
        )
        for point, elevation in bathymetry.fetch_elevations(list(distances)).items()
    ]
    written = upsert(session, GridCell, rows, overwrite=True)
    session.commit()
    logger.info(
        "grid: %d cell(s) stored, %d of them sea",
        written,
        sum(1 for row in rows if row.elevation_m < 0),
    )
    return written
