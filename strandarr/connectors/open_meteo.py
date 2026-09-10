import logging
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone

import httpx

from strandarr.connectors.http import request_json
from strandarr.models.current_observation import CurrentObservation
from strandarr.models.wind_observation import WindObservation

logger = logging.getLogger(__name__)

WEATHER_URL = "https://archive-api.open-meteo.com/v1/archive"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
WIND_HOURLY = "windspeed_10m,winddirection_10m"
CURRENT_HOURLY = "ocean_current_velocity,ocean_current_direction"
# Open-Meteo bills 1.0 API call for a request of <=10 variables covering <=14 days, whatever
# number of locations it carries, and charges pro rata beyond that (their example: "4 weeks of
# data equals 3.0 API calls"). So 14 days is the widest window still costing a single call,
# and locations are free -- which makes the quota-optimal request as wide as the URL allows.
# Measured against the live API: 500 locations is accepted (7.4kB URL), 1000 returns
# "414 Request-URI Too Large" (14.5kB). 200 keeps the URL near 3kB with plenty of headroom.
#
# Rows are yielded once per request rather than accumulated, so peak memory is bounded by one
# request's worth -- POINTS_PER_REQUEST x DAYS_PER_REQUEST x 24, about 67k rows -- however
# long the backfill is.
POINTS_PER_REQUEST = 200
DAYS_PER_REQUEST = 14


def iter_wind(
    points: list[tuple[float, float]], start: date, end: date
) -> Iterator[list[WindObservation]]:
    yield from _iter(
        WEATHER_URL, WIND_HOURLY, WindObservation, "wind", points, start, end
    )


def iter_currents(
    points: list[tuple[float, float]], start: date, end: date
) -> Iterator[list[CurrentObservation]]:
    yield from _iter(
        MARINE_URL, CURRENT_HOURLY, CurrentObservation, "currents", points, start, end
    )


def date_chunks(start: date, end: date) -> Iterator[tuple[date, date]]:
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=DAYS_PER_REQUEST - 1))
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def _iter(
    url: str,
    hourly: str,
    model: type,
    label: str,
    points: list[tuple[float, float]],
    start: date,
    end: date,
) -> Iterator[list]:
    speed_key, direction_key = hourly.split(",")
    with httpx.Client(timeout=180) as client:
        for chunk_start, chunk_end in date_chunks(start, end):
            for offset in range(0, len(points), POINTS_PER_REQUEST):
                batch = points[offset : offset + POINTS_PER_REQUEST]
                rows = [
                    model(
                        recorded_at=at,
                        lat=lat,
                        lon=lon,
                        speed=speed,
                        direction=direction,
                    )
                    for lat, lon, at, speed, direction in _fetch(
                        client,
                        url,
                        hourly,
                        speed_key,
                        direction_key,
                        batch,
                        chunk_start,
                        chunk_end,
                    )
                ]
                logger.info(
                    "open_meteo %s: %s..%s, cells %d-%d of %d -> %d rows",
                    label,
                    chunk_start,
                    chunk_end,
                    offset + 1,
                    offset + len(batch),
                    len(points),
                    len(rows),
                )
                yield rows


def _fetch(
    client: httpx.Client,
    url: str,
    hourly: str,
    speed_key: str,
    direction_key: str,
    batch: list[tuple[float, float]],
    start: date,
    end: date,
) -> list[tuple[float, float, datetime, float, float]]:
    """One request, one location batch. The caller yields per call, so nothing accumulates."""
    payload = request_json(
        client,
        "GET",
        url,
        # Open-Meteo's minutely ceiling counts locations, so this is what paces the requests.
        cost=len(batch),
        params={
            "latitude": ",".join(str(lat) for lat, _ in batch),
            "longitude": ",".join(str(lon) for _, lon in batch),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "hourly": hourly,
            "timezone": "UTC",
        },
    )
    locations = payload if isinstance(payload, list) else [payload]
    out: list[tuple[float, float, datetime, float, float]] = []
    for (lat, lon), location in zip(batch, locations):
        out.extend(_parse_location(location, lat, lon, speed_key, direction_key))
    return out


def _parse_location(
    location: dict, lat: float, lon: float, speed_key: str, direction_key: str
) -> list[tuple[float, float, datetime, float, float]]:
    """Rows are keyed on the requested grid point, not the coordinate the model snapped to,
    so wind and currents share one grid and land cells can be identified across both."""
    hourly = location.get("hourly")
    if not hourly:
        return []
    return [
        (
            lat,
            lon,
            datetime.fromisoformat(at).replace(tzinfo=timezone.utc),
            speed,
            direction,
        )
        for at, speed, direction in zip(
            hourly["time"], hourly[speed_key], hourly[direction_key]
        )
        if speed is not None and direction is not None
    ]
