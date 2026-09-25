import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from math import ceil
from typing import Any

import httpx
from sqlalchemy.orm import Session

from strandarr.config import settings
from strandarr.db import upsert
from strandarr.models import Wind
from strandarr.sources.http import request_json

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

POINTS_PER_REQUEST = 200
DAYS_PER_REQUEST = 14
PAST_HOURS = 48
TIMEOUT_SECONDS = 180
NATIVE_STEP_DEG = 0.25

VARIABLES = {
    "windspeed_10m": "speed_kmh",
    "winddirection_10m": "direction_deg",
}

Cell = tuple[int, float, float]


def cost(points: int, hours: int) -> int:
    return points * ceil(hours / 24 / DAYS_PER_REQUEST)


def _batches(cells: list[Cell], hours: int) -> Iterator[list[Wind]]:
    with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
        for offset in range(0, len(cells), POINTS_PER_REQUEST):
            batch = cells[offset : offset + POINTS_PER_REQUEST]
            payload = request_json(
                client,
                "GET",
                FORECAST_URL,
                cost=cost(len(batch), hours + PAST_HOURS),
                params={
                    "latitude": ",".join(str(lat) for _, lat, _ in batch),
                    "longitude": ",".join(str(lon) for _, _, lon in batch),
                    "hourly": ",".join(VARIABLES),
                    "timezone": "UTC",
                    "past_hours": PAST_HOURS,
                    "forecast_hours": hours,
                },
            )
            rows = parse(batch, payload)
            logger.info(
                "meteo: cells %d-%d of %d -> %d rows",
                offset + 1,
                offset + len(batch),
                len(cells),
                len(rows),
            )
            yield rows


def parse(batch: list[Cell], payload: dict[str, Any] | list[Any]) -> list[Wind]:
    locations = payload if isinstance(payload, list) else [payload]
    rows: list[Wind] = []
    for (cell_id, lat, lon), location in zip(batch, locations, strict=True):
        if not _is_requested_cell(location, lat, lon):
            continue
        hourly = location.get("hourly") or {}
        series = [hourly[variable] for variable in VARIABLES]
        for at, *values in zip(hourly["time"], *series, strict=True):
            if any(value is None for value in values):
                continue
            rows.append(
                Wind(
                    valid_at=datetime.fromisoformat(at).replace(tzinfo=UTC),
                    cell_id=cell_id,
                    forecast=True,
                    **dict(zip(VARIABLES.values(), values, strict=True)),
                )
            )
    return rows


def _is_requested_cell(location: dict[str, Any], lat: float, lon: float) -> bool:
    tolerance = max(settings.grid_step_deg, NATIVE_STEP_DEG) / 2
    offset = max(
        abs(float(location["latitude"]) - lat), abs(float(location["longitude"]) - lon)
    )
    if offset <= tolerance + 1e-9:
        return True
    logger.debug(
        "meteo: dropping cell %.3f,%.3f, answered %.3f,%.3f (%.3f deg away)",
        lat,
        lon,
        location["latitude"],
        location["longitude"],
        offset,
    )
    return False


def forecast(session: Session, cells: list[Cell]) -> int:
    total = 0
    for rows in _batches(cells, settings.forecast_hours):
        total += upsert(session, Wind, rows, overwrite=True, where=Wind.forecast)
        session.commit()
    return total
