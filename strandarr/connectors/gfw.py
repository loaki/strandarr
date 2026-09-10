import logging
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from strandarr.config import settings
from strandarr.connectors.http import request_json
from strandarr.models.vessel_position import VesselPosition

logger = logging.getLogger(__name__)

REPORT_URL = "https://gateway.api.globalfishingwatch.org/v3/4wings/report"
FISHING_DATASET = "public-global-fishing-effort:latest"


def iter_positions(
    dataset: str,
    source: str,
    bbox: tuple[float, float, float, float],
    start: date,
    end: date,
    skip_days: set[date] | None = None,
) -> Iterator[list[VesselPosition]]:
    if not settings.gfw_api_token:
        raise RuntimeError("GFW_API_TOKEN is not set")
    skip_days = skip_days or set()
    with httpx.Client(
        headers={"Authorization": f"Bearer {settings.gfw_api_token}"}, timeout=900
    ) as client:
        day = start
        while day <= end:
            if day in skip_days:
                day += timedelta(days=1)
                continue
            payload = request_json(
                client,
                "POST",
                REPORT_URL,
                params=_params(dataset, day),
                json=_body(bbox),
            )
            rows = _flatten(payload)
            parsed = [p for p in (_parse(row, source) for row in rows) if p is not None]
            positions = _collapse_cells(parsed)
            logger.info(
                "gfw %s: %s -> %d positions from %d grid cells",
                source,
                day,
                len(positions),
                len(parsed),
            )
            yield positions
            day += timedelta(days=1)


def _collapse_cells(positions: list[VesselPosition]) -> list[VesselPosition]:
    """One row per vessel-hour, which is what the dedup index stores.

    At HIGH spatial resolution a vessel that moves during the hour is reported in several
    grid cells. Keep the cell it spent the most of the hour in and carry the summed effort,
    so the position is the vessel's centre of activity and no fishing hours are lost.
    Without this the rows collide on (mmsi, recorded_at, source) and the upsert silently
    keeps an arbitrary cell.
    """
    kept: dict[tuple[str, datetime], VesselPosition] = {}
    best_effort: dict[tuple[str, datetime], float] = {}
    for position in positions:
        key = (position.mmsi, position.recorded_at)
        effort = position.effort_hours or 0.0
        current = kept.get(key)
        if current is None:
            kept[key] = position
            best_effort[key] = effort
            continue
        total = (current.effort_hours or 0.0) + effort
        if effort > best_effort[key]:
            kept[key] = position
            best_effort[key] = effort
        kept[key].effort_hours = total
    return list(kept.values())


def _params(dataset: str, day: date) -> dict:
    return {
        "datasets[0]": dataset,
        "format": "JSON",
        "group-by": "VESSEL_ID",
        "temporal-resolution": "HOURLY",
        "spatial-resolution": "HIGH",
        "date-range": f"{day.isoformat()},{(day + timedelta(days=1)).isoformat()}",
    }


def _body(bbox: tuple[float, float, float, float]) -> dict:
    min_lon, min_lat, max_lon, max_lat = bbox
    geometry = {
        "type": "Polygon",
        "coordinates": [
            [
                [min_lon, min_lat],
                [max_lon, min_lat],
                [max_lon, max_lat],
                [min_lon, max_lat],
                [min_lon, min_lat],
            ]
        ],
    }
    return {
        "geojson": {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {}, "geometry": geometry}],
        }
    }


def _parse(row: dict, source: str) -> VesselPosition | None:
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


def _flatten(node: Any) -> list[dict]:
    if isinstance(node, dict):
        if "lat" in node:
            return [node]
        out: list[dict] = []
        for value in node.values():
            out.extend(_flatten(value))
        return out
    if isinstance(node, list):
        out = []
        for value in node:
            out.extend(_flatten(value))
        return out
    return []
