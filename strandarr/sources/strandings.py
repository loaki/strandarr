import hashlib
import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

from strandarr.analysis.geo import GRID, BBox
from strandarr.db import upsert
from strandarr.models import Stranding
from strandarr.sources import GBIF, PELAGIS, species
from strandarr.sources.http import request_json, request_text
from strandarr.timeframe import DayRange

logger = logging.getLogger(__name__)


SEARCH_URL = "https://api.gbif.org/v1/occurrence/search"
PAGE_SIZE = 300
GBIF_TIMEOUT_SECONDS = 120
STRANDING_DATASET = "f6baa711-9c3f-4820-97ce-83fe50744678"


def _occurrences(params: dict[str, Any]) -> Iterator[dict[str, Any]]:
    offset = 0
    with httpx.Client(timeout=GBIF_TIMEOUT_SECONDS) as client:
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


def event_window(event_date: str) -> datetime | None:
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
    return start + (end - start) / 2


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip())
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_gbif(occurrence: dict[str, Any]) -> Stranding | None:
    lat, lon = occurrence.get("decimalLatitude"), occurrence.get("decimalLongitude")
    event_date = occurrence.get("eventDate")
    if lat is None or lon is None or not event_date:
        return None
    recorded_at = event_window(event_date)
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
    count = occurrence.get("individualCount")
    return Stranding(
        external_id=str(occurrence["key"]),
        source=GBIF,
        recorded_at=recorded_at,
        lat=float(lat),
        lon=float(lon),
        species_scientific=scientific,
        species_common=common,
        individual_count=1 if count is None else int(count),
    )


def fetch_gbif(bbox: BBox, days: DayRange) -> list[Stranding]:
    start, end = days.isoformat()
    params = {
        "datasetKey": STRANDING_DATASET,
        "geometry": bbox.wkt(),
        "eventDate": f"{start},{end}",
    }
    rows = [
        stranding
        for occurrence in _occurrences(params)
        if (stranding := parse_gbif(occurrence)) is not None
    ]
    logger.info("gbif: %s..%s -> %d strandings", start, end, len(rows))
    return rows


PELAGIS_URL = "http://pelagis.in2p3.fr/public/histo-carto/echouage_multilayers.php"
PELAGIS_TIMEOUT_SECONDS = 180

ALL_FACADES = (
    "29_56_44_85_17_33_40_64_99A_2A_2B_06_83_13_30_34_11_66_99M_27_59_62_80_76_14_50_"
    "35_22_99B_99N_988_986_987_989_984S_976_974_984T_973_975_971_972_977_978"
)

EVENT_LINE = re.compile(
    r"^\s*(\d{4}-\d{2}-\d{2})\s*/\s*(\d+)\s*/\s*([^/]+?)\s*/\s*(.+?)\s*$"
)

NOON = timedelta(hours=12)


def parse_pelagis(raw: str, bbox: BBox) -> list[Stranding]:
    seen: dict[tuple[Any, ...], int] = {}
    strandings = []
    for row in raw.split("\n")[1:]:
        fields = row.split("\t")
        if len(fields) < 4:
            continue
        try:
            lon, lat = float(fields[0]), float(fields[1])
        except ValueError:
            continue
        if not bbox.contains(lat, lon):
            continue
        for line in fields[3].split("<br>"):
            match = EVENT_LINE.match(line)
            if not match:
                continue
            day, count, label, place = match.groups()
            scientific, common = species.from_common(label)
            commune = place.partition(",")[0].strip()
            key = (lat, lon, day, common, commune)
            index = seen.get(key, 0)
            seen[key] = index + 1
            strandings.append(
                Stranding(
                    external_id=_identity(lat, lon, day, common, commune, index),
                    source=PELAGIS,
                    recorded_at=datetime.fromisoformat(day).replace(tzinfo=UTC) + NOON,
                    lat=lat,
                    lon=lon,
                    species_scientific=scientific,
                    species_common=common,
                    individual_count=int(count),
                )
            )
    return strandings


def _identity(
    lat: float, lon: float, day: str, common: str, commune: str, index: int
) -> str:
    payload = f"{lat:.5f}|{lon:.5f}|{day}|{common}|{commune}|{index}"
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def fetch_pelagis(bbox: BBox, days: DayRange) -> list[Stranding]:
    start, end = days.isoformat()
    with httpx.Client(timeout=PELAGIS_TIMEOUT_SECONDS) as client:
        raw = request_text(
            client,
            "GET",
            PELAGIS_URL,
            params={
                "date_inf": start,
                "date_sup": end,
                "nb_mam_inf": 1,
                "nb_mam_sup": 100000,
                "ordre1": "",
                "famille1": "",
                "lb_nom1": "",
                "ordre2": "",
                "famille2": "",
                "lb_nom2": "",
                "facade1": ALL_FACADES,
                "nom_facade1": "Toutes",
            },
        )
    rows = parse_pelagis(raw, bbox)
    logger.info("pelagis: %s..%s -> %d strandings", start, end, len(rows))
    return rows


def ingest_gbif(session: Session, days: DayRange) -> int:
    return _store(session, fetch_gbif(GRID.bbox, days))


def ingest_pelagis(session: Session, days: DayRange) -> int:
    return _store(session, fetch_pelagis(GRID.bbox, days))


def _store(session: Session, rows: list[Stranding]) -> int:
    written = upsert(session, Stranding, rows, overwrite=True)
    session.commit()
    return written
