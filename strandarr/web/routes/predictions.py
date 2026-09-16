from typing import Any

from fastapi import APIRouter

from strandarr.analysis import climatology as climatology_model
from strandarr.db.queries import prediction
from strandarr.services import coverage
from strandarr.web.deps import Db, Hour, hour
from strandarr.web.serialize import collection

router = APIRouter()

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
        prediction.climatology(db, climatology_model.slot(day) + 1),
        CLIMATOLOGY_PROPS,
        peak_of="probability",
        window_days=climatology_model.WINDOW_DAYS,
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
