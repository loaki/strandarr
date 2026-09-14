import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from strandarr import sources
from strandarr.config import settings
from strandarr.connectors.http import request_json
from strandarr.models import MarineCondition

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"

POINTS_PER_REQUEST = 200
DAYS_PER_REQUEST = 14
TIMEOUT_SECONDS = 180

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
    source: str
    url: str
    variables: dict[str, str]
    native_step_deg: float

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(self.variables.values())


ERA5_STEP_DEG = 0.25

MARINE_STEP_DEG = 1 / 12

WEATHER_ARCHIVE = Product(
    sources.WEATHER_ARCHIVE, ARCHIVE_URL, WIND_VARIABLES, ERA5_STEP_DEG
)
MARINE_ARCHIVE = Product(
    sources.MARINE_ARCHIVE, MARINE_URL, SEA_VARIABLES, MARINE_STEP_DEG
)
WEATHER_FORECAST = Product(
    sources.WEATHER_FORECAST, FORECAST_URL, WIND_VARIABLES, ERA5_STEP_DEG
)
MARINE_FORECAST = Product(
    sources.MARINE_FORECAST, MARINE_URL, SEA_VARIABLES, MARINE_STEP_DEG
)

FORECASTS = (WEATHER_FORECAST, MARINE_FORECAST)


def iter_archive(
    product: Product, points: list[tuple[float, float]], start: date, end: date
) -> Iterator[list[MarineCondition]]:
    for chunk_start, chunk_end in date_chunks(start, end):
        window = {
            "start_date": chunk_start.isoformat(),
            "end_date": chunk_end.isoformat(),
        }
        label = f"{chunk_start}..{chunk_end}"
        yield from _iter_batches(product, points, window, label)


def iter_forecast(
    product: Product, points: list[tuple[float, float]], hours: int
) -> Iterator[list[MarineCondition]]:
    yield from _iter_batches(product, points, {"forecast_hours": hours}, f"+{hours}h")


def date_chunks(start: date, end: date) -> Iterator[tuple[date, date]]:
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=DAYS_PER_REQUEST - 1))
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def _iter_batches(
    product: Product,
    points: list[tuple[float, float]],
    window: dict[str, Any],
    label: str,
) -> Iterator[list[MarineCondition]]:
    with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
        for offset in range(0, len(points), POINTS_PER_REQUEST):
            batch = points[offset : offset + POINTS_PER_REQUEST]
            issued_at = datetime.now(UTC)
            payload = request_json(
                client,
                "GET",
                product.url,
                cost=len(batch),
                params={
                    "latitude": ",".join(str(lat) for lat, _ in batch),
                    "longitude": ",".join(str(lon) for _, lon in batch),
                    "hourly": ",".join(product.variables),
                    "timezone": "UTC",
                    **window,
                },
            )
            rows = parse(product, batch, payload, issued_at)
            logger.info(
                "open_meteo %s: %s, cells %d-%d of %d -> %d rows",
                product.source,
                label,
                offset + 1,
                offset + len(batch),
                len(points),
                len(rows),
            )
            yield rows


def parse(
    product: Product,
    batch: list[tuple[float, float]],
    payload: dict[str, Any] | list[Any],
    issued_at: datetime,
) -> list[MarineCondition]:
    locations = payload if isinstance(payload, list) else [payload]
    rows: list[MarineCondition] = []
    for (lat, lon), location in zip(batch, locations, strict=True):
        if not _is_requested_cell(product, location, lat, lon):
            continue
        hourly = location.get("hourly") or {}
        series = [hourly[variable] for variable in product.variables]
        for at, *values in zip(hourly["time"], *series, strict=True):
            if all(value is None for value in values):
                continue
            rows.append(
                MarineCondition(
                    valid_at=datetime.fromisoformat(at).replace(tzinfo=UTC),
                    issued_at=issued_at,
                    source=product.source,
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
        "open_meteo: dropping cell %.3f,%.3f, answered %.3f,%.3f (%.3f deg away)",
        lat,
        lon,
        location["latitude"],
        location["longitude"],
        offset,
    )
    return False
