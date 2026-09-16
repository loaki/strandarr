from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter

from strandarr import sources
from strandarr.db.queries import environment, observation
from strandarr.models import MarineCondition
from strandarr.timeframe import DayRange
from strandarr.web.deps import Day, Db, Hour, hour_window
from strandarr.web.serialize import pick

router = APIRouter()

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
