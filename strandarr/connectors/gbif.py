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
            for occurrence in page["results"]:
                stranding = _parse(occurrence)
                if stranding is not None:
                    strandings.append(stranding)
            if page.get("endOfRecords", True):
                break
            offset += PAGE_SIZE
    logger.info("gbif: %s..%s -> %d strandings", start, end, len(strandings))
    return strandings


def _parse(occurrence: dict) -> Stranding | None:
    lat, lon = occurrence.get("decimalLatitude"), occurrence.get("decimalLongitude")
    event_date = occurrence.get("eventDate")
    if lat is None or lon is None or not event_date:
        return None
    recorded_at = datetime.fromisoformat(event_date[:19])
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=timezone.utc)
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
