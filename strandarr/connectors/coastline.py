import hashlib
import logging
from collections.abc import Iterator
from typing import Any

import httpx

from strandarr.connectors.http import request_json
from strandarr.geo import NearestIndex, Point, bearing_deg, densify, distance_km
from strandarr.grid import in_bbox
from strandarr.models import CoastalSegment

logger = logging.getLogger(__name__)

COASTLINE_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    "v5.1.2/geojson/ne_10m_coastline.geojson"
)
TIMEOUT_SECONDS = 120

SEGMENT_LENGTH_KM = 10.0
DENSIFY_KM = 2.0
REGION_MARGIN_DEG = 4.0


def fetch_lines() -> list[list[Point]]:
    with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
        payload = request_json(client, "GET", COASTLINE_URL)
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"coastline: expected an object, got {type(payload).__name__}"
        )
    lines = [
        line
        for feature in payload.get("features", [])
        for line in _lines(feature.get("geometry") or {})
        if any(in_bbox(lat, lon, REGION_MARGIN_DEG) for lat, lon in line)
    ]
    logger.info("coastline: %d line(s) near the region", len(lines))
    return lines


def nearest_index(lines: list[list[Point]]) -> NearestIndex:
    index = NearestIndex(point for line in lines for point in densify(line, DENSIFY_KM))
    logger.info("coastline: %d densified point(s) indexed", index.size)
    return index


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
    return [s for s in closed if in_bbox(s.center_lat, s.center_lon)]


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
        coastline_orientation_deg=bearing_deg(start, end),
        path=[[round(lon, 5), round(lat, 5)] for lat, lon in points],
    )
