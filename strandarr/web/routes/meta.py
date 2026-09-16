from datetime import datetime

from fastapi import APIRouter

from strandarr.analysis import climatology, drift, forecast
from strandarr.db.queries import observation
from strandarr.web.deps import Db

router = APIRouter()

VERSIONS = {
    "drift": drift.MODEL_VERSION,
    "climatology": climatology.MODEL_VERSION,
    "forecast": forecast.MODEL_VERSION,
}


@router.get("/range")
def get_range(db: Db) -> dict[str, datetime | None]:
    return observation.observed_range(db)


@router.get("/versions")
def get_versions() -> dict[str, str]:
    return VERSIONS
