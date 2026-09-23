from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import numpy as np
from fastapi import Depends, FastAPI, Query
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from strandarr.analysis import risk
from strandarr.analysis.risk import SIGNALS
from strandarr.db import Session
from strandarr.models import (
    CoastalSegment,
    Condition,
    SegmentRisk,
    Stranding,
    VesselPosition,
)
from strandarr.timeframe import DayRange

STATIC_DIR = Path(__file__).resolve().parent / "static"


def session() -> Iterator[OrmSession]:
    with Session() as open_session:
        yield open_session


Db = Annotated[OrmSession, Depends(session)]
Hour = Annotated[datetime, Query()]
Day = Annotated[date, Query()]

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

SEA_STATE_FIELDS = Condition.MEASUREMENTS

app = FastAPI(title="strandarr")

# The index is a fixed rescale of the stored probability, so it lives with the
# coefficients rather than in a column. Refitting changes it for every day at
# once, which is why `strandarr fit` tells you to re-run the risk jobs.
RISK_MODEL = risk.Model.load()


def _hour(at: datetime) -> datetime:
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at.replace(minute=0, second=0, microsecond=0)


def _window(at: datetime) -> tuple[datetime, datetime]:
    start = _hour(at)
    return start, start + timedelta(hours=1)


def _rows(items: Sequence[Any], fields: Sequence[str]) -> list[dict[str, Any]]:
    return [{field: getattr(item, field) for field in fields} for item in items]


@app.get("/api/range")
def get_range(db: Db) -> dict[str, datetime | None]:
    bounds = [
        db.execute(select(func.min(column), func.max(column))).one()
        for column in (Condition.valid_at, VesselPosition.recorded_at)
    ]
    lows = [low for low, _ in bounds if low is not None]
    highs = [high for _, high in bounds if high is not None]
    return {"min": min(lows) if lows else None, "max": max(highs) if highs else None}


@app.get("/api/risk")
def get_risk(day: Day, db: Db) -> dict[str, Any]:
    rows = db.execute(
        select(
            CoastalSegment.id,
            CoastalSegment.center_lat,
            CoastalSegment.center_lon,
            CoastalSegment.length_km,
            CoastalSegment.path,
            SegmentRisk.probability,
            SegmentRisk.seasonal,
            SegmentRisk.source,
            *(getattr(SegmentRisk, name) for name in SIGNALS),
        )
        .join(SegmentRisk, SegmentRisk.coastal_segment_id == CoastalSegment.id)
        .where(SegmentRisk.day == day)
        .order_by(SegmentRisk.probability.desc())
    ).all()

    low, high = risk.index_scale(db)
    index = risk.index_of(
        np.array([float(row[5]) for row in rows], dtype=np.float64), low, high
    )
    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": row[4] or [[row[2], row[1]]],
            },
            "properties": {
                "segment_id": row[0],
                "lat": row[1],
                "lon": row[2],
                "length_km": row[3],
                "index": float(index[position]),
                "probability": float(row[5]),
                "seasonal": float(row[6]),
                "source": row[7],
                **{
                    name: float(value)
                    for name, value in zip(SIGNALS, row[8:], strict=True)
                },
            },
        }
        for position, row in enumerate(rows)
    ]
    return {
        "type": "FeatureCollection",
        "features": features,
        "fitted": RISK_MODEL.fitted,
    }


@app.get("/api/strandings")
def get_strandings(day: Day, db: Db) -> list[dict[str, Any]]:
    start, end = DayRange.of(day).bounds()
    rows = db.execute(
        select(Stranding).where(
            Stranding.recorded_at >= start, Stranding.recorded_at < end
        )
    ).scalars()
    return _rows(list(rows), STRANDING_FIELDS)


@app.get("/api/strandings/monthly")
def get_strandings_monthly(db: Db) -> list[dict[str, Any]]:
    year = func.extract("year", Stranding.recorded_at)
    month = func.extract("month", Stranding.recorded_at)
    rows = db.execute(
        select(year.label("year"), month.label("month"), func.sum(Stranding.individual_count))
        .group_by(year, month)
        .order_by(year, month)
    ).all()
    by_year: dict[int, dict[int, int]] = {}
    for year_number, month_number, total in rows:
        by_year.setdefault(int(year_number), {})[int(month_number)] = int(total)
    if not by_year:
        return []

    # The most recent year is still in progress: months after the last one with
    # any report are "not yet happened", not a genuine drop to zero.
    latest_year = max(by_year)
    latest_month = max(by_year[latest_year])
    return [
        {
            "year": year_number,
            "months": [
                None if year_number == latest_year and m > latest_month
                else months.get(m, 0)
                for m in range(1, 13)
            ],
        }
        for year_number, months in sorted(by_year.items())
    ]


@app.get("/api/vessels")
def get_vessels(at: Hour, db: Db) -> list[dict[str, Any]]:
    start, end = _window(at)
    rows = db.execute(
        select(VesselPosition).where(
            VesselPosition.recorded_at >= start, VesselPosition.recorded_at < end
        )
    ).scalars()
    return _rows(list(rows), VESSEL_FIELDS)


@app.get("/api/conditions")
def get_conditions(at: Hour, db: Db) -> list[dict[str, Any]]:
    start, end = _window(at)
    rows = db.execute(
        select(
            Condition.lat,
            Condition.lon,
            Condition.forecast,
            *(getattr(Condition, name) for name in SEA_STATE_FIELDS),
        ).where(Condition.valid_at >= start, Condition.valid_at < end)
    ).all()
    return [
        {
            "lat": row[0],
            "lon": row[1],
            "forecast": row[2],
            **dict(zip(SEA_STATE_FIELDS, row[3:], strict=True)),
        }
        for row in rows
    ]


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
