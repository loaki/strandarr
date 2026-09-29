import logging
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from strandarr.analysis.geo import GRID, BBox
from strandarr.config import settings
from strandarr.db import upsert
from strandarr.models import GfwVessel, Vessel, VesselPosition
from strandarr.sources import GFW_FISHING
from strandarr.sources.http import request_json, request_object

logger = logging.getLogger(__name__)

REPORT_URL = "https://gateway.api.globalfishingwatch.org/v3/4wings/report"
FISHING_DATASET = "public-global-fishing-effort:latest"
VESSELS_URL = "https://gateway.api.globalfishingwatch.org/v3/vessels"
IDENTITY_DATASET = "public-global-vessel-identity:latest"
IDS_PER_LOOKUP = 50
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


@dataclass(frozen=True)
class Identity:
    mmsi: str
    ship_name: str | None
    flag: str | None
    gear_type: str | None
    vessel_type: str | None


def parse(row: dict[str, Any]) -> Position | None:
    vessel_id, lat, lon = row.get("vesselId"), row.get("lat"), row.get("lon")
    date_text = row.get("date")
    if not vessel_id or lat is None or lon is None or not date_text:
        return None
    recorded_at = datetime.fromisoformat(date_text.replace("Z", "+00:00"))
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=UTC)
    return {
        "gfw_id": str(vessel_id),
        "recorded_at": recorded_at,
        "lat": float(lat),
        "lon": float(lon),
        "effort_hours": float(row.get("hours") or 0.0),
    }


def collapse_cells(positions: list[Position]) -> list[Position]:
    best: dict[tuple[int, datetime], Position] = {}
    total: dict[tuple[int, datetime], float] = {}
    for position in positions:
        key = (position["vessel_id"], position["recorded_at"])
        total[key] = total.get(key, 0.0) + position["effort_hours"]
        incumbent = best.get(key)
        if incumbent is None or position["effort_hours"] > incumbent["effort_hours"]:
            best[key] = position
    for key, position in best.items():
        position["effort_hours"] = total[key]
    return list(best.values())


def _newest(items: list[dict[str, Any]] | None) -> str | None:
    if not items:
        return None
    name = max(items, key=lambda item: item.get("yearTo") or 0).get("name")
    return str(name).lower() if name else None


def _entry_identities(entry: dict[str, Any]) -> dict[str, Identity]:
    reported = [
        info for info in entry.get("selfReportedInfo") or [] if info.get("ssvid")
    ]
    if not reported:
        return {}
    current = max(
        reported,
        key=lambda info: (
            bool(info.get("latestVesselInfo")),
            info.get("transmissionDateTo") or "",
        ),
    )
    return {
        combined["vesselId"]: Identity(
            mmsi=str(current["ssvid"]),
            ship_name=current.get("shipname") or None,
            flag=current.get("flag") or None,
            gear_type=_newest(combined.get("geartypes")),
            vessel_type=_newest(combined.get("shiptypes")),
        )
        for combined in entry.get("combinedSourcesInfo") or []
        if combined.get("vesselId")
    }


def identities(client: httpx.Client, gfw_ids: list[str]) -> dict[str, Identity]:
    found: dict[str, Identity] = {}
    for offset in range(0, len(gfw_ids), IDS_PER_LOOKUP):
        params: dict[str, Any] = {"datasets[0]": IDENTITY_DATASET}
        for index, gfw_id in enumerate(gfw_ids[offset : offset + IDS_PER_LOOKUP]):
            params[f"ids[{index}]"] = gfw_id
        payload = request_object(
            client, "GET", VESSELS_URL, "gfw vessel identity", params=params
        )
        for entry in payload.get("entries") or []:
            found.update(_entry_identities(entry))
    return {gfw_id: found[gfw_id] for gfw_id in gfw_ids if gfw_id in found}


def register(session: Session, found: dict[str, Identity]) -> dict[str, int]:
    if not found:
        return {}
    by_mmsi: dict[str, dict[str, str | None]] = {}
    for identity in found.values():
        merged = by_mmsi.setdefault(identity.mmsi, {})
        for field, value in asdict(identity).items():
            if merged.get(field) is None:
                merged[field] = value
    values = insert(Vessel).values(list(by_mmsi.values()))
    statement = values.on_conflict_do_update(
        index_elements=["mmsi"],
        set_={
            field: func.coalesce(values.excluded[field], Vessel.__table__.c[field])
            for field in Vessel.IDENTITY
        },
    ).returning(Vessel.mmsi, Vessel.id)
    stored = dict(session.execute(statement).tuples().all())
    mapping = {gfw_id: stored[identity.mmsi] for gfw_id, identity in found.items()}
    session.execute(
        insert(GfwVessel)
        .values([{"gfw_id": gfw_id, "vessel_id": v} for gfw_id, v in mapping.items()])
        .on_conflict_do_nothing(index_elements=["gfw_id"])
    )
    return mapping


def _known(session: Session, gfw_ids: list[str]) -> dict[str, int]:
    rows = session.execute(
        select(GfwVessel.gfw_id, GfwVessel.vessel_id).where(
            GfwVessel.gfw_id.in_(gfw_ids)
        )
    )
    return dict(rows.tuples().all())


def resolve(
    session: Session, client: httpx.Client, cells: list[Position]
) -> list[Position]:
    wanted = sorted({cell["gfw_id"] for cell in cells})
    known = _known(session, wanted)
    unknown = [gfw_id for gfw_id in wanted if gfw_id not in known]
    if unknown:
        found = register(session, identities(client, unknown))
        known.update(found)
        for gfw_id in unknown:
            if gfw_id not in found:
                logger.error("gfw: no mmsi found for vessel %s, skipping it", gfw_id)
        logger.info(
            "gfw: looked up %d new vessel(s), %d resolved", len(unknown), len(found)
        )
    return [
        {**cell, "vessel_id": known[cell["gfw_id"]]}
        for cell in cells
        if cell["gfw_id"] in known
    ]


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
        cells = [
            parsed for row in flatten(payload) if (parsed := parse(row)) is not None
        ]
        positions = collapse_cells(resolve(session, client, cells))
    written = upsert(
        session,
        VesselPosition,
        [
            VesselPosition(
                vessel_id=position["vessel_id"],
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
