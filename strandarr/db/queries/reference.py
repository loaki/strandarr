import logging
from typing import Any, cast

from sqlalchemy import CursorResult, delete, or_, select
from sqlalchemy.orm import Session

from strandarr.analysis.coast import SegmentIndex
from strandarr.analysis.geo import GRID, Point
from strandarr.config import settings
from strandarr.models import CoastalSegment, GridCell

logger = logging.getLogger(__name__)


def segments(session: Session) -> SegmentIndex:
    rows = list(
        session.execute(select(CoastalSegment).order_by(CoastalSegment.id)).scalars()
    )
    if not rows:
        raise RuntimeError(
            "no coastal segments stored: run `strandarr reference` first"
        )
    return SegmentIndex.of(rows)


def ingest_points(session: Session) -> list[Point]:
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
    return [GRID.cell(lat, lon) for lat, lon in stored]


def prune_segments(session: Session, keep: set[str]) -> int:
    removed = cast(
        "CursorResult[Any]",
        session.execute(
            delete(CoastalSegment).where(CoastalSegment.external_id.notin_(keep))
        ),
    ).rowcount
    if removed:
        logger.info("coastline: removed %d segment(s) no longer on the coast", removed)
    return int(removed)
