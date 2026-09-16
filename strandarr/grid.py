from dataclasses import dataclass

from strandarr.config import settings
from strandarr.geo import BBox, Point

BBOX = BBox(-6.0, 42.0, 9.5, 51.5)

COORD_DECIMALS = 4


@dataclass(frozen=True)
class Grid:
    bbox: BBox
    step_deg: float

    def cell(self, lat: float, lon: float) -> Point:
        return round(lat, COORD_DECIMALS), round(lon, COORD_DECIMALS)

    def axes(self) -> tuple[list[float], list[float]]:
        min_lon, min_lat, max_lon, max_lat = self.bbox.corners()
        rows = round((max_lat - min_lat) / self.step_deg) + 1
        columns = round((max_lon - min_lon) / self.step_deg) + 1
        return (
            [min_lat + row * self.step_deg for row in range(rows)],
            [min_lon + column * self.step_deg for column in range(columns)],
        )

    def points(self) -> list[Point]:
        lats, lons = self.axes()
        return [self.cell(lat, lon) for lat in lats for lon in lons]


GRID = Grid(BBOX, settings.grid_step_deg)
