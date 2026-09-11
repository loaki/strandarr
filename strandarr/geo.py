import math
from collections.abc import Iterable, Iterator
from itertools import pairwise

EARTH_RADIUS_KM = 6371.0088

Point = tuple[float, float]


def distance_km(first: Point, second: Point) -> float:
    lat1, lon1 = map(math.radians, first)
    lat2, lon2 = map(math.radians, second)
    half_chord = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(half_chord))


def bearing_deg(first: Point, second: Point) -> float:
    (lat1, lon1), (lat2, lon2) = first, second
    east = math.cos(math.radians((lat1 + lat2) / 2)) * (lon2 - lon1)
    return math.degrees(math.atan2(east, lat2 - lat1)) % 360


def densify(line: list[Point], spacing_km: float) -> Iterator[Point]:
    if not line:
        return
    yield line[0]
    for start, end in pairwise(line):
        span = distance_km(start, end)
        for step in range(1, int(span / spacing_km) + 1):
            fraction = step * spacing_km / span
            yield (
                start[0] + (end[0] - start[0]) * fraction,
                start[1] + (end[1] - start[1]) * fraction,
            )
        yield end


class NearestIndex:
    def __init__(self, points: Iterable[Point], tile_deg: float = 0.5):
        self.tile_deg = tile_deg
        self.tiles: dict[tuple[int, int], list[Point]] = {}
        self.size = 0
        for point in points:
            self.tiles.setdefault(self._tile(point), []).append(point)
            self.size += 1

    def _tile(self, point: Point) -> tuple[int, int]:
        return math.floor(point[0] / self.tile_deg), math.floor(
            point[1] / self.tile_deg
        )

    def distance_km(self, point: Point, limit_km: float) -> float | None:
        center = self._tile(point)
        tile_km = self.tile_deg * 111.0
        best: float | None = None
        for ring in range(int(limit_km / tile_km) + 2):
            if best is not None and best <= (ring - 1) * tile_km:
                break
            for candidate in _ring_tiles(center, ring):
                for other in self.tiles.get(candidate, ()):
                    found = distance_km(point, other)
                    if best is None or found < best:
                        best = found
        return best if best is not None and best <= limit_km else None


def _ring_tiles(center: tuple[int, int], ring: int) -> Iterator[tuple[int, int]]:
    row, column = center
    if ring == 0:
        yield center
        return
    for offset in range(-ring, ring + 1):
        yield row - ring, column + offset
        yield row + ring, column + offset
    for offset in range(-ring + 1, ring):
        yield row + offset, column - ring
        yield row + offset, column + ring
