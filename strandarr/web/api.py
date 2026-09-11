from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session as SessionType

from strandarr.models import (
    CurrentObservation,
    Stranding,
    VesselPosition,
    WindObservation,
)
from strandarr.models.base import Observation
from strandarr.repositories.db import Session
from strandarr.services.schedule import GRID_STEP_DEG

app = FastAPI(title="strandarr")

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


def _bounds(
    session: SessionType, model: type[Observation]
) -> tuple[datetime | None, datetime | None]:
    low, high = session.execute(
        select(func.min(model.recorded_at), func.max(model.recorded_at))
    ).one()
    return low, high


@app.get("/api/range")
def get_range() -> dict:
    with Session() as session:
        bounds = [
            _bounds(session, VesselPosition),
            _bounds(session, WindObservation),
            _bounds(session, CurrentObservation),
        ]
    mins = [b[0] for b in bounds if b[0] is not None]
    maxs = [b[1] for b in bounds if b[1] is not None]
    return {
        "min": min(mins) if mins else None,
        "max": max(maxs) if maxs else None,
        "grid_step": GRID_STEP_DEG,
    }


def _hour_points(
    model: type[Observation], hour: datetime, fields: list[str]
) -> list[dict]:
    with Session() as session:
        stmt = select(model).where(
            model.recorded_at >= hour, model.recorded_at < hour + timedelta(hours=1)
        )
        return [
            {f: getattr(row, f) for f in fields}
            for row in session.execute(stmt).scalars()
        ]


@app.get("/api/vessels")
def get_vessels(hour: datetime = Query(...)) -> list[dict]:
    return _hour_points(
        VesselPosition,
        hour,
        [
            "mmsi",
            "lat",
            "lon",
            "ship_name",
            "flag",
            "gear_type",
            "vessel_type",
            "effort_hours",
            "source",
        ],
    )


@app.get("/api/wind")
def get_wind(hour: datetime = Query(...)) -> list[dict]:
    return _hour_points(WindObservation, hour, ["lat", "lon", "speed", "direction"])


@app.get("/api/currents")
def get_currents(hour: datetime = Query(...)) -> list[dict]:
    return _hour_points(CurrentObservation, hour, ["lat", "lon", "speed", "direction"])


@app.get("/api/strandings")
def get_strandings(day: date = Query(...)) -> list[dict]:
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)
    with Session() as session:
        stmt = select(Stranding).where(
            Stranding.recorded_at >= start, Stranding.recorded_at < end
        )
        return [
            {
                "lat": row.lat,
                "lon": row.lon,
                "species_scientific": row.species_scientific,
                "species_common": row.species_common,
                "individual_count": row.individual_count,
                "recorded_at": row.recorded_at,
                "source": row.source,
                "external_id": row.external_id,
            }
            for row in session.execute(stmt).scalars()
        ]


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
