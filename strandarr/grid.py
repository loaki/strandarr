from strandarr.config import settings
from strandarr.geo import Point

BBOX = (-6.0, 42.0, 9.5, 51.5)

COORD_DECIMALS = 4


def cell(lat: float, lon: float) -> Point:
    return round(lat, COORD_DECIMALS), round(lon, COORD_DECIMALS)


def grid_points() -> list[Point]:
    step = settings.grid_step_deg
    min_lon, min_lat, max_lon, max_lat = BBOX
    rows = round((max_lat - min_lat) / step)
    columns = round((max_lon - min_lon) / step)
    return [
        cell(min_lat + row * step, min_lon + column * step)
        for row in range(rows + 1)
        for column in range(columns + 1)
    ]


def in_bbox(lat: float, lon: float, margin_deg: float = 0.0) -> bool:
    min_lon, min_lat, max_lon, max_lat = BBOX
    return (
        min_lat - margin_deg <= lat <= max_lat + margin_deg
        and min_lon - margin_deg <= lon <= max_lon + margin_deg
    )
