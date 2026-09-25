import hashlib
import logging
from collections.abc import Iterator
from typing import Any, cast

import httpx
from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.orm import Session

from strandarr.analysis.geo import (
    GRID,
    Point,
    bearing_deg,
    distance_km,
    in_french_coast,
)
from strandarr.db import upsert
from strandarr.models import CoastalSegment
from strandarr.sources.http import request_object

logger = logging.getLogger(__name__)

COASTLINE_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    "v5.1.2/geojson/ne_10m_coastline.geojson"
)
TIMEOUT_SECONDS = 120

SEGMENT_LENGTH_KM = 10.0
DENSIFY_KM = 2.0
REGION_MARGIN_DEG = 4.0

# drift_daily and segment_risk cascade from coastal_segment, so a coastline that
# comes back short would take years of simulation with it. The coastline is a
# pinned file that should never move; a prune this large means the fetch is wrong,
# not the coast.
MAX_PRUNE_SHARE = 0.05
MIN_PRUNE_SEGMENTS = 5


def fetch_lines() -> list[list[Point]]:
    with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
        payload = request_object(client, "GET", COASTLINE_URL, "coastline")
    lines = [
        line
        for feature in payload.get("features", [])
        for line in _lines(feature.get("geometry") or {})
        if any(GRID.bbox.contains(lat, lon, REGION_MARGIN_DEG) for lat, lon in line)
    ]
    logger.info("coastline: %d line(s) near the region", len(lines))
    return lines


def _lines(geometry: dict[str, Any]) -> Iterator[list[Point]]:
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if kind == "LineString":
        yield [(float(lat), float(lon)) for lon, lat, *_ in coordinates]
    elif kind == "MultiLineString":
        for line in coordinates:
            yield [(float(lat), float(lon)) for lon, lat, *_ in line]


def segments(lines: list[list[Point]]) -> list[CoastalSegment]:
    return [segment for line in lines for segment in _segments(line)]


def _segments(line: list[Point]) -> list[CoastalSegment]:
    closed: list[CoastalSegment] = []
    points: list[Point] = []
    length = 0.0
    for point in line:
        if points:
            length += distance_km(points[-1], point)
        points.append(point)
        if length >= SEGMENT_LENGTH_KM:
            closed.append(_segment(points, length))
            points = [point]
            length = 0.0
    if len(points) > 1 and length >= SEGMENT_LENGTH_KM / 2:
        closed.append(_segment(points, length))
    return [s for s in closed if in_french_coast(s.center_lat, s.center_lon)]


def _segment(points: list[Point], length_km: float) -> CoastalSegment:
    center_lat = sum(lat for lat, _ in points) / len(points)
    center_lon = sum(lon for _, lon in points) / len(points)
    start, end = points[0], points[-1]
    external_id = hashlib.sha256(
        f"{start[1]:.5f},{start[0]:.5f}|{end[1]:.5f},{end[0]:.5f}".encode()
    ).hexdigest()[:32]
    return CoastalSegment(
        external_id=external_id,
        center_lat=center_lat,
        center_lon=center_lon,
        length_km=length_km,
        orientation_deg=bearing_deg(start, end),
        path=[[round(lon, 5), round(lat, 5)] for lat, lon in points],
    )


def build(session: Session) -> int:
    built = segments(fetch_lines())
    if not built:
        raise RuntimeError(
            "coastline: the source yielded no segments, refusing to store"
        )

    stored = session.execute(
        select(func.count()).select_from(CoastalSegment)
    ).scalar_one()
    keep = {segment.external_id for segment in built}
    stale = session.execute(
        select(func.count())
        .select_from(CoastalSegment)
        .where(CoastalSegment.external_id.notin_(keep))
    ).scalar_one()
    _guard_prune(stored, stale)

    written = upsert(session, CoastalSegment, built, overwrite=True)
    removed = cast(
        "CursorResult[Any]",
        session.execute(
            delete(CoastalSegment).where(CoastalSegment.external_id.notin_(keep))
        ),
    ).rowcount
    session.commit()
    logger.info("coastline: %d segment(s) stored, %d stale removed", written, removed)
    return written


def _guard_prune(stored: int, stale: int) -> None:
    allowed = max(MIN_PRUNE_SEGMENTS, int(MAX_PRUNE_SHARE * stored))
    if stale > allowed:
        raise RuntimeError(
            f"coastline: would remove {stale} of {stored} stored segment(s), "
            f"more than the {allowed} allowed. Deleting a segment cascades to its "
            f"drift and risk history, so this is refused. Check the coastline "
            f"source, or clear the table deliberately if the change is intended"
        )
