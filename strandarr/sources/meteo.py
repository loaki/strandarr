import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from typing import Any

import httpx
from sqlalchemy.orm import Session

from strandarr.analysis.geo import Point
from strandarr.config import settings
from strandarr.db import upsert
from strandarr.models import Condition
from strandarr.sources.http import request_json
from strandarr.timeframe import DayRange

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"

POINTS_PER_REQUEST = 200
DAYS_PER_REQUEST = 14
VARIABLES_PER_REQUEST = 10
TIMEOUT_SECONDS = 180

ERA5_STEP_DEG = 0.25
MARINE_STEP_DEG = 1 / 12

WIND_VARIABLES = {
    "windspeed_10m": "wind_speed_kmh",
    "winddirection_10m": "wind_direction_deg",
}
SEA_VARIABLES = {
    "ocean_current_velocity": "current_speed_kmh",
    "ocean_current_direction": "current_direction_deg",
    "wave_height": "wave_height_m",
    "wave_direction": "wave_direction_deg",
    "wave_period": "wave_period_s",
    "swell_wave_height": "swell_height_m",
    "swell_wave_direction": "swell_direction_deg",
    "swell_wave_period": "swell_period_s",
    "sea_surface_temperature": "sea_surface_temperature_c",
    "sea_level_height_msl": "sea_level_m",
}


@dataclass(frozen=True)
class Product:
    name: str
    url: str
    variables: dict[str, str]
    native_step_deg: float

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(self.variables.values())


WEATHER_ARCHIVE = Product("weather_archive", ARCHIVE_URL, WIND_VARIABLES, ERA5_STEP_DEG)
MARINE_ARCHIVE = Product("marine_archive", MARINE_URL, SEA_VARIABLES, MARINE_STEP_DEG)
WEATHER_FORECAST = Product(
    "weather_forecast", FORECAST_URL, WIND_VARIABLES, ERA5_STEP_DEG
)
MARINE_FORECAST = Product("marine_forecast", MARINE_URL, SEA_VARIABLES, MARINE_STEP_DEG)

FORECASTS = (WEATHER_FORECAST, MARINE_FORECAST)


def cost(points: int, days: int, variables: int) -> int:
    return (
        points * ceil(days / DAYS_PER_REQUEST) * ceil(variables / VARIABLES_PER_REQUEST)
    )


def _batches(
    product: Product, points: list[Point], window: dict[str, Any], label: str, days: int
) -> Iterator[list[Condition]]:
    with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
        for offset in range(0, len(points), POINTS_PER_REQUEST):
            batch = points[offset : offset + POINTS_PER_REQUEST]
            payload = request_json(
                client,
                "GET",
                product.url,
                cost=cost(len(batch), days, len(product.variables)),
                params={
                    "latitude": ",".join(str(lat) for lat, _ in batch),
                    "longitude": ",".join(str(lon) for _, lon in batch),
                    "hourly": ",".join(product.variables),
                    "timezone": "UTC",
                    **window,
                },
            )
            rows = parse(product, batch, payload)
            logger.info(
                "meteo %s: %s, cells %d-%d of %d -> %d rows",
                product.name,
                label,
                offset + 1,
                offset + len(batch),
                len(points),
                len(rows),
            )
            yield rows


def parse(
    product: Product, batch: list[Point], payload: dict[str, Any] | list[Any]
) -> list[Condition]:
    locations = payload if isinstance(payload, list) else [payload]
    rows: list[Condition] = []
    for (lat, lon), location in zip(batch, locations, strict=True):
        if not _is_requested_cell(product, location, lat, lon):
            continue
        hourly = location.get("hourly") or {}
        series = [hourly[variable] for variable in product.variables]
        for at, *values in zip(hourly["time"], *series, strict=True):
            if all(value is None for value in values):
                continue
            rows.append(
                Condition(
                    valid_at=datetime.fromisoformat(at).replace(tzinfo=UTC),
                    lat=lat,
                    lon=lon,
                    **dict(zip(product.columns, values, strict=True)),
                )
            )
    return rows


def _is_requested_cell(
    product: Product, location: dict[str, Any], lat: float, lon: float
) -> bool:
    tolerance = max(settings.grid_step_deg, product.native_step_deg) / 2
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


def _write(
    session: Session, product: Product, rows: list[Condition], live: bool
) -> int:
    for row in rows:
        row.forecast = live
    written = upsert(
        session,
        Condition,
        rows,
        overwrite=True,
        only=(*product.columns, "forecast"),
        where=Condition.forecast if live else None,
    )
    session.commit()
    return written


def archive(
    session: Session, product: Product, points: list[Point], days: DayRange
) -> int:
    start, end = days.isoformat()
    window = {"start_date": start, "end_date": end}
    return sum(
        _write(session, product, rows, live=False)
        for rows in _batches(product, points, window, f"{start}..{end}", len(days))
    )


def forecast(session: Session, points: list[Point]) -> int:
    hours = settings.forecast_hours
    total = 0
    for product in FORECASTS:
        window = {"forecast_hours": hours}
        total += sum(
            _write(session, product, rows, live=True)
            for rows in _batches(
                product, points, window, f"+{hours}h", ceil(hours / 24)
            )
        )
    return total
