import logging
from datetime import date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from strandarr.analysis.geo import GRID, BBox
from strandarr.config import settings
from strandarr.db import upsert
from strandarr.models import Vessel, VesselPosition
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


Position = dict[str, Any]


def parse(row: dict[str, Any]) -> Position | None:
    mmsi, lat, lon = row.get("mmsi"), row.get("lat"), row.get("lon")
    recorded_at = row.get("date")
    if not mmsi or lat is None or lon is None or not recorded_at:
        return None
    return {
        "mmsi": str(mmsi),
        "recorded_at": datetime.fromisoformat(recorded_at.replace("Z", "+00:00")),
        "lat": float(lat),
        "lon": float(lon),
        "effort_hours": float(row.get("hours") or 0.0),
        "ship_name": row.get("shipName") or None,
        "flag": row.get("flag") or None,
        "gear_type": row.get("geartype") or None,
        "vessel_type": row.get("vesselType") or None,
    }


def collapse_cells(positions: list[Position]) -> list[Position]:
    best: dict[tuple[str, datetime], Position] = {}
    total: dict[tuple[str, datetime], float] = {}
    for position in positions:
        key = (position["mmsi"], position["recorded_at"])
        total[key] = total.get(key, 0.0) + position["effort_hours"]
        incumbent = best.get(key)
        if incumbent is None or position["effort_hours"] > incumbent["effort_hours"]:
            best[key] = position
    for key, position in best.items():
        position["effort_hours"] = total[key]
    return list(best.values())


def vessels(session: Session, positions: list[Position]) -> dict[str, int]:
    identities = {
        position["mmsi"]: {
            "mmsi": position["mmsi"],
            **{field: position[field] for field in Vessel.IDENTITY},
        }
        for position in positions
    }
    if not identities:
        return {}
    values = insert(Vessel).values(list(identities.values()))
    statement = values.on_conflict_do_update(
        index_elements=["mmsi"],
        set_={field: values.excluded[field] for field in Vessel.IDENTITY},
    ).returning(Vessel.mmsi, Vessel.id)
    return dict(session.execute(statement).tuples().all())


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
    known = vessels(session, positions)
    written = upsert(
        session,
        VesselPosition,
        [
            VesselPosition(
                vessel_id=known[position["mmsi"]],
                recorded_at=position["recorded_at"],
                source=GFW_FISHING,
                lat=position["lat"],
                lon=position["lon"],
                effort_hours=position["effort_hours"],
            )
            for position in positions
        ],
        overwrite=True,
    )
    session.commit()
    logger.info(
        "gfw: %s -> %d vessel-hours from %d grid cells", day, written, len(cells)
    )
    return written
