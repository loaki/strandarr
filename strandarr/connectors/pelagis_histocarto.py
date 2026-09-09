import logging
import re
from datetime import date, datetime, timezone

import httpx

from strandarr import species
from strandarr.connectors.http import request_text
from strandarr.models.stranding import Stranding

logger = logging.getLogger(__name__)

BASE_URL = "http://pelagis.in2p3.fr/public/histo-carto/echouage_multilayers.php"

ALL_FACADES = (
    "29_56_44_85_17_33_40_64_99A_2A_2B_06_83_13_30_34_11_66_99M_27_59_62_80_76_14_50_35_22_99B_99N_"
    "988_986_987_989_984S_976_974_984T_973_975_971_972_977_978"
)

EVENT_LINE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s*/\s*(\d+)\s*/\s*([^/]+?)\s*/\s*(.+?)\s*$")


def fetch_strandings(
    bbox: tuple[float, float, float, float], start: date, end: date
) -> list[Stranding]:
    params = {
        "date_inf": start.isoformat(),
        "date_sup": end.isoformat(),
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
    }
    with httpx.Client(timeout=180) as client:
        raw = request_text(client, "GET", BASE_URL, params=params)

    strandings = _parse(raw, bbox)
    logger.info(
        "pelagis_histocarto: %s..%s -> %d strandings", start, end, len(strandings)
    )
    return strandings


def _parse(raw: str, bbox: tuple[float, float, float, float]) -> list[Stranding]:
    min_lon, min_lat, max_lon, max_lat = bbox
    seen: dict[tuple, int] = {}
    out = []
    for row in raw.split("\n")[1:]:
        fields = row.split("\t")
        if len(fields) < 4:
            continue
        try:
            lon, lat = float(fields[0]), float(fields[1])
        except ValueError:
            continue
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            continue
        for line in fields[3].split("<br>"):
            match = EVENT_LINE.match(line)
            if not match:
                continue
            day, count, species_raw, place = match.groups()
            scientific, common = species.from_common(species_raw)
            commune = place.partition(",")[0].strip()
            key = (day, common, commune)
            index = seen.get(key, 0)
            seen[key] = index + 1
            out.append(
                Stranding(
                    external_id=f"{day}|{common}|{commune}|{index}",
                    source="pelagis_histocarto",
                    recorded_at=datetime.fromisoformat(day).replace(tzinfo=timezone.utc),
                    lat=lat,
                    lon=lon,
                    species_scientific=scientific,
                    species_common=common,
                    individual_count=int(count),
                )
            )
    return out
