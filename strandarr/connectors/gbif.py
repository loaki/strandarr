import logging
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from strandarr import sources, species
from strandarr.connectors.http import request_json
from strandarr.models import Stranding

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.gbif.org/v1/occurrence/search"
PAGE_SIZE = 300
TIMEOUT_SECONDS = 120

STRANDING_DATASET = "f6baa711-9c3f-4820-97ce-83fe50744678"

SOURCE = sources.GBIF


def _geometry(bbox: tuple[float, float, float, float]) -> str:
    min_lon, min_lat, max_lon, max_lat = bbox
    return (
        f"POLYGON(({min_lon} {min_lat},{max_lon} {min_lat},{max_lon} {max_lat},"
        f"{min_lon} {max_lat},{min_lon} {min_lat}))"
    )


def _iter_occurrences(params: dict[str, Any], label: str) -> Iterator[dict[str, Any]]:
    offset = 0
    with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
        while True:
            page = request_json(
                client,
                "GET",
                SEARCH_URL,
                params={**params, "limit": PAGE_SIZE, "offset": offset},
            )
            if not isinstance(page, dict):
                raise RuntimeError(
                    f"gbif: expected an object, got {type(page).__name__}"
                )
            yield from page["results"]
            if page.get("endOfRecords", True):
                break
            offset += PAGE_SIZE
            if offset % (PAGE_SIZE * 20) == 0:
                logger.info("gbif %s: %d records so far", label, offset)


def fetch_strandings(
    bbox: tuple[float, float, float, float], start: date, end: date
) -> list[Stranding]:
    params = {
        "datasetKey": STRANDING_DATASET,
        "geometry": _geometry(bbox),
        "eventDate": f"{start.isoformat()},{end.isoformat()}",
    }
    strandings = [
        stranding
        for stranding in map(parse, _iter_occurrences(params, "strandings"))
        if stranding is not None
    ]
    logger.info("gbif: %s..%s -> %d strandings", start, end, len(strandings))
    return strandings


def event_window(event_date: str) -> tuple[datetime, float] | None:
    first, _, last = event_date.partition("/")
    try:
        start = _instant(first)
        end = _instant(last) if last.strip() else start
    except ValueError:
        return None
    if end < start:
        return None
    if len(last.strip() or first.strip()) == 10:
        end += timedelta(days=1)
    half_width = (end - start) / 2
    return start + half_width, half_width.total_seconds() / 3600


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip())
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse(occurrence: dict[str, Any]) -> Stranding | None:
    lat, lon = occurrence.get("decimalLatitude"), occurrence.get("decimalLongitude")
    event_date = occurrence.get("eventDate")
    if lat is None or lon is None or not event_date:
        return None
    window = event_window(event_date)
    if window is None:
        logger.warning(
            "gbif: skipping %s, unusable eventDate %r",
            occurrence.get("key"),
            event_date,
        )
        return None
    recorded_at, time_uncertainty_hours = window
    scientific, common = species.from_scientific(
        occurrence.get("species") or occurrence.get("scientificName")
    )
    count = occurrence.get("individualCount")
    return Stranding(
        external_id=str(occurrence["key"]),
        source=SOURCE,
        recorded_at=recorded_at,
        lat=float(lat),
        lon=float(lon),
        species_scientific=scientific,
        species_common=common,
        individual_count=1 if count is None else int(count),
        coordinate_uncertainty_m=occurrence.get("coordinateUncertaintyInMeters"),
        coordinate_precision_deg=occurrence.get("coordinatePrecision"),
        time_uncertainty_hours=time_uncertainty_hours,
        location_precision="gbif_reported",
    )
