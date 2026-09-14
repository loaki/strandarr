import math
from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query
from fastapi.staticfiles import StaticFiles
from sqlalchemy import SQLColumnExpression, func, select
from sqlalchemy.orm import Session as SessionType

from strandarr import sources
from strandarr.analysis import drift
from strandarr.models import (
    CoastalSegment,
    DriftArrival,
    MarineCondition,
    Stranding,
    VesselPosition,
)
from strandarr.repositories.db import Session

app = FastAPI(title="strandarr")

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

VESSEL_FIELDS = (
    "mmsi",
    "lat",
    "lon",
    "ship_name",
    "flag",
    "gear_type",
    "vessel_type",
    "effort_hours",
    "recorded_at",
    "source",
)

STRANDING_FIELDS = (
    "lat",
    "lon",
    "species_scientific",
    "species_common",
    "individual_count",
    "recorded_at",
    "time_uncertainty_hours",
    "coordinate_uncertainty_m",
    "location_precision",
    "source",
    "external_id",
)


def session() -> Iterator[SessionType]:
    with Session() as open_session:
        yield open_session


Db = Annotated[SessionType, Depends(session)]
Hour = Annotated[datetime, Query()]


def _hour(at: datetime) -> tuple[datetime, datetime]:
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    start = at.replace(minute=0, second=0, microsecond=0)
    return start, start + timedelta(hours=1)


def _bounds(
    db: SessionType, when: SQLColumnExpression[datetime]
) -> tuple[datetime | None, datetime | None]:
    low, high = db.execute(select(func.min(when), func.max(when))).one()
    return low, high


@app.get("/api/range")
def get_range(db: Db) -> dict[str, Any]:
    bounds = [
        _bounds(db, MarineCondition.valid_at),
        _bounds(db, VesselPosition.recorded_at),
    ]
    lows = [low for low, _ in bounds if low is not None]
    highs = [high for _, high in bounds if high is not None]
    return {
        "min": min(lows) if lows else None,
        "max": max(highs) if highs else None,
    }


@app.get("/api/conditions")
def get_conditions(at: Hour, db: Db) -> list[dict[str, Any]]:
    start, end = _hour(at)
    rows = db.execute(
        select(MarineCondition).where(
            MarineCondition.valid_at >= start, MarineCondition.valid_at < end
        )
    ).scalars()
    return merge_conditions(rows)


def merge_conditions(rows: Iterable[MarineCondition]) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: sources.precedence(row.source))
    cells: dict[tuple[float, float], dict[str, Any]] = {}
    for row in ordered:
        cell = cells.setdefault(
            (row.lat, row.lon),
            {
                "lat": row.lat,
                "lon": row.lon,
                "sources": [],
                **dict.fromkeys(MarineCondition.MEASUREMENTS),
            },
        )
        used = False
        for name in MarineCondition.MEASUREMENTS:
            value = getattr(row, name)
            if value is not None and cell[name] is None:
                cell[name] = value
                used = True
        if used:
            cell["sources"].append(row.source)
    return list(cells.values())


@app.get("/api/vessels")
def get_vessels(at: Hour, db: Db) -> list[dict[str, Any]]:
    start, end = _hour(at)
    rows = db.execute(
        select(VesselPosition).where(
            VesselPosition.recorded_at >= start, VesselPosition.recorded_at < end
        )
    ).scalars()
    return [{field: getattr(row, field) for field in VESSEL_FIELDS} for row in rows]


@app.get("/api/risk")
def get_risk(at: Hour, db: Db) -> dict[str, Any]:
    start, end = _hour(at)
    expected = func.sum(DriftArrival.expected_count)
    rows = db.execute(
        select(
            CoastalSegment.id,
            CoastalSegment.center_lat,
            CoastalSegment.center_lon,
            CoastalSegment.length_km,
            CoastalSegment.path,
            expected.label("expected_count"),
            func.count().label("release_days"),
        )
        .join(DriftArrival, DriftArrival.coastal_segment_id == CoastalSegment.id)
        .where(
            DriftArrival.arrival_at >= start,
            DriftArrival.arrival_at < end,
            DriftArrival.model_version == drift.MODEL_VERSION,
        )
        .group_by(CoastalSegment.id)
        .order_by(expected.desc())
    ).all()

    features: list[dict[str, Any]] = [
        {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": path or [[center_lon, center_lat]],
            },
            "properties": {
                "segment_id": segment_id,
                "lat": center_lat,
                "lon": center_lon,
                "length_km": length_km,
                "expected_count": float(count),
                "probability": 1.0 - math.exp(-float(count)),
                "release_days": days,
            },
        }
        for segment_id, center_lat, center_lon, length_km, path, count, days in rows
    ]
    peak = max((float(row.expected_count) for row in rows), default=0.0)
    return {"type": "FeatureCollection", "features": features, "peak": peak}


@app.get("/api/strandings")
def get_strandings(day: Annotated[date, Query()], db: Db) -> list[dict[str, Any]]:
    start = datetime.combine(day, time.min, tzinfo=UTC)
    rows = db.execute(
        select(Stranding).where(
            Stranding.recorded_at >= start,
            Stranding.recorded_at < start + timedelta(days=1),
        )
    ).scalars()
    return [{field: getattr(row, field) for field in STRANDING_FIELDS} for row in rows]


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
