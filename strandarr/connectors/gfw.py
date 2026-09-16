import logging
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from strandarr.analysis.geo import BBox
from strandarr.analysis.timeframe import DayRange
from strandarr.config import settings
from strandarr.connectors.http import request_json
from strandarr.models import VesselPosition

logger = logging.getLogger(__name__)

REPORT_URL = "https://gateway.api.globalfishingwatch.org/v3/4wings/report"
FISHING_DATASET = "public-global-fishing-effort:latest"
TIMEOUT_SECONDS = 300


def iter_positions(
    dataset: str,
    source: str,
    bbox: BBox,
    days: DayRange,
    skip: set[date] | None = None,
) -> Iterator[tuple[date, list[VesselPosition]]]:
    if not settings.gfw_api_token:
        raise RuntimeError("GFW_API_TOKEN is not set")
    skipped = skip or set()
    with httpx.Client(
        headers={"Authorization": f"Bearer {settings.gfw_api_token}"},
        timeout=TIMEOUT_SECONDS,
    ) as client:
        for day in days:
            if day not in skipped:
                payload = request_json(
                    client,
                    "POST",
                    REPORT_URL,
                    params=_params(dataset, day),
                    json=_body(bbox),
                )
                parsed = (parse(row, source) for row in flatten(payload))
                cells = [position for position in parsed if position is not None]
                positions = collapse_cells(cells)
                logger.info(
                    "gfw %s: %s -> %d positions from %d grid cells",
                    source,
                    day,
                    len(positions),
                    len(cells),
                )
                yield day, positions


def collapse_cells(positions: list[VesselPosition]) -> list[VesselPosition]:
    best: dict[tuple[str, datetime], VesselPosition] = {}
    total_effort: dict[tuple[str, datetime], float] = {}
    for position in positions:
        key = (position.mmsi, position.recorded_at)
        effort = position.effort_hours or 0.0
        total_effort[key] = total_effort.get(key, 0.0) + effort
        incumbent = best.get(key)
        if incumbent is None or effort > (incumbent.effort_hours or 0.0):
            best[key] = position
    for key, position in best.items():
        position.effort_hours = total_effort[key]
    return list(best.values())


def _params(dataset: str, day: date) -> dict[str, Any]:
    return {
        "datasets[0]": dataset,
        "format": "JSON",
        "group-by": "VESSEL_ID",
        "temporal-resolution": "HOURLY",
        "spatial-resolution": "HIGH",
        "date-range": f"{day.isoformat()},{(day + timedelta(days=1)).isoformat()}",
    }


def _body(bbox: BBox) -> dict[str, Any]:
    return {
        "geojson": {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {}, "geometry": bbox.geojson()}
            ],
        }
    }


def parse(row: dict[str, Any], source: str) -> VesselPosition | None:
    mmsi, lat, lon = row.get("mmsi"), row.get("lat"), row.get("lon")
    recorded_at = row.get("date")
    if not mmsi or lat is None or lon is None or not recorded_at:
        return None
    return VesselPosition(
        mmsi=str(mmsi),
        recorded_at=datetime.fromisoformat(recorded_at.replace("Z", "+00:00")),
        lat=float(lat),
        lon=float(lon),
        source=source,
        ship_name=row.get("shipName") or None,
        flag=row.get("flag") or None,
        gear_type=row.get("geartype") or None,
        vessel_type=row.get("vesselType") or None,
        effort_hours=row.get("hours"),
    )


def flatten(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, dict):
        if "lat" in node:
            return [node]
        return [row for value in node.values() for row in flatten(value)]
    if isinstance(node, list):
        return [row for value in node for row in flatten(value)]
    return []
