from collections.abc import Iterable
from datetime import datetime
from typing import Any

from fastapi import APIRouter

from strandarr.analysis import climatology, drift, forecast
from strandarr.analysis.timeframe import DayRange
from strandarr.connectors import sources
from strandarr.db.queries import coverage, environment, observation, prediction
from strandarr.models import MarineCondition
from strandarr.web.deps import Day, Db, Hour, hour, hour_window
from strandarr.web.serialize import collection, pick

router = APIRouter(prefix="/api")

VERSIONS = {
    "drift": drift.MODEL_VERSION,
    "climatology": climatology.MODEL_VERSION,
    "forecast": forecast.MODEL_VERSION,
}

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

RISK_PROPS = {"drift_index": float, "release_days": int}

CLIMATOLOGY_PROPS = {
    "probability": float,
    "expected_per_day": float,
    "observed": float,
    "years": int,
}

FORECAST_PROPS = {
    "probability": float,
    "persistence": float,
    "drift_index": float,
    "swell_m": float,
    "onshore_m": float,
}

RANKED = 50


@router.get("/range")
def get_range(db: Db) -> dict[str, datetime | None]:
    return observation.observed_range(db)


@router.get("/versions")
def get_versions() -> dict[str, str]:
    return VERSIONS


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


@router.get("/conditions")
def get_conditions(at: Hour, db: Db) -> list[dict[str, Any]]:
    return merge_conditions(environment.in_hour(db, hour_window(at)))


@router.get("/vessels")
def get_vessels(at: Hour, db: Db) -> list[dict[str, Any]]:
    return pick(observation.vessels(db, hour_window(at)), VESSEL_FIELDS)


@router.get("/strandings")
def get_strandings(day: Day, db: Db) -> list[dict[str, Any]]:
    return pick(
        observation.strandings(db, DayRange(day, day).bounds()), STRANDING_FIELDS
    )


@router.get("/risk")
def get_risk(at: Hour, db: Db) -> dict[str, Any]:
    day = hour(at).date()
    needed, missing = coverage.missing_releases(db, day)
    return collection(
        prediction.risk(db, day),
        RISK_PROPS,
        peak_of="drift_index",
        relative_of="drift_index",
        release_days_expected=len(needed),
        complete=not missing,
        missing_release_days=[day.isoformat() for day in missing],
    )


@router.get("/climatology")
def get_climatology(at: Hour, db: Db) -> dict[str, Any]:
    day = hour(at).date()
    return collection(
        prediction.climatology(db, climatology.slot(day) + 1),
        CLIMATOLOGY_PROPS,
        peak_of="probability",
        window_days=climatology.WINDOW_DAYS,
    )


@router.get("/forecast")
def get_forecast(at: Hour, db: Db) -> dict[str, Any]:
    payload = collection(
        prediction.forecast(db, hour(at).date()), FORECAST_PROPS, peak_of="probability"
    )
    payload["rank"] = [
        feature["properties"]["segment_id"] for feature in payload["features"][:RANKED]
    ]
    return payload
