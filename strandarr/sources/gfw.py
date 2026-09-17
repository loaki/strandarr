import logging
from datetime import date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

from strandarr.analysis.geo import GRID, BBox
from strandarr.config import settings
from strandarr.db import upsert
from strandarr.models import VesselPosition
from strandarr.sources import GFW_FISHING
from strandarr.sources.http import request_json

logger = logging.getLogger(__name__)

REPORT_URL = "https://gateway.api.globalfishingwatch.org/v3/4wings/report"
FISHING_DATASET = "public-global-fishing-effort:latest"
TIMEOUT_SECONDS = 300


def _params(day: date) -> dict[str, Any]:
    return {
        "datasets[0]": FISHING_DATASET,
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


def flatten(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, dict):
        if "lat" in node:
            return [node]
        return [row for value in node.values() for row in flatten(value)]
    if isinstance(node, list):
        return [row for value in node for row in flatten(value)]
    return []


def parse(row: dict[str, Any]) -> VesselPosition | None:
    mmsi, lat, lon = row.get("mmsi"), row.get("lat"), row.get("lon")
    recorded_at = row.get("date")
    if not mmsi or lat is None or lon is None or not recorded_at:
        return None
    return VesselPosition(
        mmsi=str(mmsi),
        recorded_at=datetime.fromisoformat(recorded_at.replace("Z", "+00:00")),
        lat=float(lat),
        lon=float(lon),
        source=GFW_FISHING,
        ship_name=row.get("shipName") or None,
        flag=row.get("flag") or None,
        gear_type=row.get("geartype") or None,
        vessel_type=row.get("vesselType") or None,
        effort_hours=row.get("hours"),
    )


def collapse_cells(positions: list[VesselPosition]) -> list[VesselPosition]:
    best: dict[tuple[str, datetime], VesselPosition] = {}
    total: dict[tuple[str, datetime], float] = {}
    for position in positions:
        key = (position.mmsi, position.recorded_at)
        effort = position.effort_hours or 0.0
        total[key] = total.get(key, 0.0) + effort
        incumbent = best.get(key)
        if incumbent is None or effort > (incumbent.effort_hours or 0.0):
            best[key] = position
    for key, position in best.items():
        position.effort_hours = total[key]
    return list(best.values())


def ingest(session: Session, day: date) -> int:
    if not settings.gfw_api_token:
        raise RuntimeError("GFW_API_TOKEN is not set")
    with httpx.Client(
        headers={"Authorization": f"Bearer {settings.gfw_api_token}"},
        timeout=TIMEOUT_SECONDS,
    ) as client:
        payload = request_json(
            client, "POST", REPORT_URL, params=_params(day), json=_body(GRID.bbox)
        )
    cells = [parsed for row in flatten(payload) if (parsed := parse(row)) is not None]
    positions = collapse_cells(cells)
    written = upsert(session, VesselPosition, positions, overwrite=True)
    session.commit()
    logger.info(
        "gfw: %s -> %d vessel-hours from %d grid cells", day, written, len(cells)
    )
    return written
