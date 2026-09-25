#!/usr/bin/env python
"""Rebuild strandarr/grid_points.json from the coastline and a bathymetry source.

Run it when GRID_STEP_DEG, MAX_DISTANCE_TO_COAST_KM, COASTAL_LAND_MARGIN_KM or
the bbox change. It keeps every sea cell within MAX_DISTANCE_TO_COAST_KM of the
shore, plus land cells within COASTAL_LAND_MARGIN_KM. Sea or land comes from
GEBCO through Open Topo Data.
"""

import json
import logging
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strandarr import log
from strandarr.analysis.geo import (
    GRID,
    GRID_POINTS_PATH,
    NearestIndex,
    Point,
    densify,
)
from strandarr.config import settings
from strandarr.sources.coastline import DENSIFY_KM, fetch_lines
from strandarr.sources.http import request_object

logger = logging.getLogger("build-grid")

ELEVATION_URL = "https://api.opentopodata.org/v1/gebco2020"
POINTS_PER_REQUEST = 100
TIMEOUT_SECONDS = 60


def elevations(points: list[Point]) -> dict[Point, float]:
    found: dict[Point, float] = {}
    with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
        for offset in range(0, len(points), POINTS_PER_REQUEST):
            batch = points[offset : offset + POINTS_PER_REQUEST]
            payload = request_object(
                client,
                "GET",
                ELEVATION_URL,
                "bathymetry",
                params={
                    "locations": "|".join(f"{lat},{lon}" for lat, lon in batch),
                },
            )
            for point, result in zip(batch, payload["results"], strict=True):
                found[point] = float(result["elevation"] or 0.0)
            logger.info("elevation: %d of %d cell(s)", len(found), len(points))
    return found


def main() -> int:
    log.setup()
    limit = settings.max_distance_to_coast_km
    margin = settings.coastal_land_margin_km

    shore = NearestIndex(
        point for line in fetch_lines() for point in densify(line, DENSIFY_KM)
    )
    logger.info("coastline: %d densified point(s) indexed", shore.size)
    near: dict[Point, float] = {}
    for point in GRID.points():
        distance = shore.distance_km(point, limit)
        if distance is not None:
            near[point] = distance
    logger.info(
        "grid: %d of %d cell(s) within %.0f km", len(near), len(GRID.points()), limit
    )

    deep = elevations(list(near))
    points = sorted(
        point
        for point, distance in near.items()
        if deep.get(point, 0.0) < 0.0 or distance <= margin
    )

    GRID_POINTS_PATH.write_text(
        json.dumps(
            {
                "source": f"{ELEVATION_URL} + Natural Earth coastline",
                "max_distance_to_coast_km": limit,
                "coastal_land_margin_km": margin,
                "grid_step_deg": settings.grid_step_deg,
                "points": [[lat, lon] for lat, lon in points],
            }
        )
        + "\n"
    )
    logger.info("wrote %d grid point(s) to %s", len(points), GRID_POINTS_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
