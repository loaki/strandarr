from collections.abc import Iterable
from datetime import datetime
from typing import Any

from fastapi import APIRouter

from strandarr.analysis import drift, risk
from strandarr.analysis.timeframe import DayRange
from strandarr.connectors import sources
from strandarr.db.queries import coverage, environment, observation, prediction
from strandarr.models import MarineCondition
from strandarr.web.deps import Day, Db, Hour, hour, hour_window
from strandarr.web.serialize import collection, pick

router = APIRouter(prefix="/api")

VERSIONS = {
    "drift": drift.MODEL_VERSION,
    "risk": risk.MODEL_VERSION,
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

RISK_PROPS = {
    "probability": float,
    "seasonal": float,
    "drift_index": float,
    "persistence": float,
    "swell_m": float,
    "onshore_m": float,
    "source": str,
}


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
    releases = coverage.releases(db, day)
    return collection(
        prediction.risk(db, day),
        RISK_PROPS,
        peak_of="probability",
        relative_of="probability",
        release_days_expected=len(releases.needed),
        complete=not releases.missing and not releases.provisional,
        missing_release_days=[day.isoformat() for day in releases.missing],
        provisional_release_days=[day.isoformat() for day in releases.provisional],
    )
