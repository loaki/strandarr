import logging

import httpx

from strandarr.connectors.http import request_object
from strandarr.geo import Point

logger = logging.getLogger(__name__)

ELEVATION_URL = "https://api.opentopodata.org/v1/gebco2020"
POINTS_PER_REQUEST = 100

TIMEOUT_SECONDS = 60


def fetch_elevations(points: list[Point]) -> dict[Point, float]:
    elevations: dict[Point, float] = {}
    with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
        for offset in range(0, len(points), POINTS_PER_REQUEST):
            batch = points[offset : offset + POINTS_PER_REQUEST]
            payload = request_object(
                client,
                "GET",
                ELEVATION_URL,
                "bathymetry",
                cost=len(batch),
                params={
                    "locations": "|".join(f"{lat},{lon}" for lat, lon in batch),
                },
            )
            if payload.get("status") != "OK":
                raise RuntimeError(f"bathymetry: {payload.get('error', payload)}")
            results = payload.get("results", [])
            for point, result in zip(batch, results, strict=True):
                elevation = result.get("elevation")
                if elevation is not None:
                    elevations[point] = float(elevation)
            logger.info(
                "bathymetry: cells %d-%d of %d",
                offset + 1,
                offset + len(batch),
                len(points),
            )
    return elevations
