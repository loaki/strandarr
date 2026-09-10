import logging
from datetime import date, datetime, timezone

import httpx

from strandarr import species
from strandarr.connectors.http import request_json
from strandarr.models.stranding import Stranding

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.gbif.org/v1/occurrence/search"
DATASET_KEY = "f6baa711-9c3f-4820-97ce-83fe50744678"
PAGE_SIZE = 300


def fetch_strandings(
    bbox: tuple[float, float, float, float], start: date, end: date
) -> list[Stranding]:
    min_lon, min_lat, max_lon, max_lat = bbox
    geometry = (
        f"POLYGON(({min_lon} {min_lat},{max_lon} {min_lat},{max_lon} {max_lat},"
        f"{min_lon} {max_lat},{min_lon} {min_lat}))"
    )

    strandings = []
    offset = 0
    with httpx.Client(timeout=120) as client:
        while True:
            page = request_json(
                client,
                "GET",
                SEARCH_URL,
                params={
                    "datasetKey": DATASET_KEY,
                    "geometry": geometry,
                    "eventDate": f"{start.isoformat()},{end.isoformat()}",
                    "limit": PAGE_SIZE,
                    "offset": offset,
                },
            )
            if not isinstance(page, dict):
                raise RuntimeError(
                    f"gbif: expected an object, got {type(page).__name__}"
                )
            for occurrence in page["results"]:
                stranding = _parse(occurrence)
                if stranding is not None:
                    strandings.append(stranding)
            if page.get("endOfRecords", True):
                break
            offset += PAGE_SIZE
    logger.info("gbif: %s..%s -> %d strandings", start, end, len(strandings))
    return strandings


def _event_date(event_date: str) -> datetime | None:
    """GBIF eventDate is free-ish text: a plain date, a timestamp with or without offset,
    or a range ("2020-01-01/2020-01-05"), of which we take the start. Anything else -- a
    month-only value, an empty interval -- is unusable, and one bad record must not fail
    the whole page."""
    value = event_date.split("/", 1)[0].strip()
    try:
        recorded_at = datetime.fromisoformat(value)
    except ValueError:
        return None
    if recorded_at.tzinfo is None:
        return recorded_at.replace(tzinfo=timezone.utc)
    return recorded_at.astimezone(timezone.utc)


def _parse(occurrence: dict) -> Stranding | None:
    lat, lon = occurrence.get("decimalLatitude"), occurrence.get("decimalLongitude")
    event_date = occurrence.get("eventDate")
    if lat is None or lon is None or not event_date:
        return None
    recorded_at = _event_date(event_date)
    if recorded_at is None:
        logger.warning(
            "gbif: skipping %s, unusable eventDate %r",
            occurrence.get("key"),
            event_date,
        )
        return None
    scientific, common = species.from_scientific(
        occurrence.get("species") or occurrence.get("scientificName")
    )
    return Stranding(
        external_id=str(occurrence["key"]),
        source="gbif",
        recorded_at=recorded_at,
        lat=float(lat),
        lon=float(lon),
        species_scientific=scientific,
        species_common=common,
        individual_count=occurrence.get("individualCount") or 1,
    )
