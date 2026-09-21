import json
import logging
import math
import pathlib
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from strandarr.analysis import Float, Int
from strandarr.config import settings
from strandarr.models import CoastalSegment

logger = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0088
KM_PER_LAT_DEG = 110.6
KM_PER_LON_DEG = 111.3

Point = tuple[float, float]
Path = list[list[float]]


@dataclass(frozen=True)
class BBox:
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    def contains(self, lat: float, lon: float, margin_deg: float = 0.0) -> bool:
        return (
            self.min_lat - margin_deg <= lat <= self.max_lat + margin_deg
            and self.min_lon - margin_deg <= lon <= self.max_lon + margin_deg
        )

    def corners(self) -> tuple[float, float, float, float]:
        return self.min_lon, self.min_lat, self.max_lon, self.max_lat

    def ring(self) -> list[list[float]]:
        return [
            [self.min_lon, self.min_lat],
            [self.max_lon, self.min_lat],
            [self.max_lon, self.max_lat],
            [self.min_lon, self.max_lat],
            [self.min_lon, self.min_lat],
        ]

    def wkt(self) -> str:
        return f"POLYGON(({','.join(f'{lon} {lat}' for lon, lat in self.ring())}))"

    def geojson(self) -> dict[str, Any]:
        return {"type": "Polygon", "coordinates": [self.ring()]}


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

TILE_DEG = 0.5


class NearestIndex:
    def __init__(self, points: Iterable[Point], tile_deg: float = TILE_DEG) -> None:
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


def shore_index(segments: "SegmentIndex") -> NearestIndex:
    return NearestIndex(
        (lat, lon)
        for position in range(len(segments))
        for lon, lat in segments.geometry(position)
    )


GRID_POINTS_PATH = pathlib.Path(__file__).resolve().parent.parent / "grid_points.json"


def stored_points() -> list[Point] | None:
    """The cells actually worth requesting, decided once and shipped.

    Which cells are sea is a bathymetry question, and answering it needs an
    elevation source we do not want to depend on at run time. `scripts/build-grid.py`
    answers it once and writes the list here. Without the file every cell within
    reach of the shore is sampled, inland ones included -- correct, but it roughly
    doubles the requests and stores rows the marine model cannot fill.
    """
    if not GRID_POINTS_PATH.exists():
        return None
    raw = json.loads(GRID_POINTS_PATH.read_text())
    points = [GRID.cell(float(lat), float(lon)) for lat, lon in raw["points"]]
    logger.info(
        "grid: %d of %d cells, from %s",
        len(points),
        len(GRID.points()),
        GRID_POINTS_PATH.name,
    )
    return points


_points: dict[tuple[int, ...], list[Point]] = {}


def grid_points(segments: "SegmentIndex") -> list[Point]:
    cached = _points.get(segments.ids)
    if cached is None:
        # Only fall back to measuring distance to the shore, which means indexing
        # every densified coastline point, when the decided grid is not shipped.
        cached = stored_points()
        if cached is None:
            cached = sampled_points(shore_index(segments))
        _points.clear()
        _points[segments.ids] = cached
    return cached


def sampled_points(index: NearestIndex) -> list[Point]:
    total = len(GRID.points())
    limit = settings.max_distance_to_coast_km
    points = [
        point for point in GRID.points() if index.distance_km(point, limit) is not None
    ]
    logger.warning(
        "grid: %s is missing, falling back to every one of the %d of %d cells "
        "within %.0f km of shore, inland cells included",
        GRID_POINTS_PATH.name,
        len(points),
        total,
        limit,
    )
    return points


@dataclass(frozen=True)
class SegmentIndex:
    ids: tuple[int, ...]
    lats: Float
    lons: Float
    lengths: Float
    orientations: Float
    paths: tuple[Path | None, ...]
    position: Mapping[int, int]

    @classmethod
    def of(cls, rows: Sequence[Any]) -> "SegmentIndex":
        return cls(
            ids=tuple(row.id for row in rows),
            lats=np.array([row.center_lat for row in rows], dtype=np.float64),
            lons=np.array([row.center_lon for row in rows], dtype=np.float64),
            lengths=np.array([row.length_km or 0.0 for row in rows], dtype=np.float64),
            orientations=np.array(
                [row.orientation_deg or 0.0 for row in rows], dtype=np.float64
            ),
            paths=tuple(row.path for row in rows),
            position={row.id: position for position, row in enumerate(rows)},
        )

    def __len__(self) -> int:
        return len(self.ids)

    def blank(self) -> Float:
        return np.zeros(len(self), dtype=np.float64)

    def geometry(self, position: int) -> Path:
        return self.paths[position] or [
            [float(self.lons[position]), float(self.lats[position])]
        ]

    def vector(self, by_id: Mapping[int, float]) -> Float:
        row = self.blank()
        for segment_id, value in by_id.items():
            position = self.position.get(segment_id)
            if position is not None:
                row[position] = value
        return row


RASTER_DEG = 0.02


@dataclass(frozen=True)
class Coast:
    segment_ids: tuple[int, ...]
    nearest: Int
    distance_km: Float

    def lookup(self, lat: Float, lon: Float) -> tuple[Int, Float]:
        min_lon, min_lat, _, _ = GRID.bbox.corners()
        rows, columns = self.nearest.shape
        i = np.clip(np.rint((lat - min_lat) / RASTER_DEG), 0, rows - 1).astype(np.int32)
        j = np.clip(np.rint((lon - min_lon) / RASTER_DEG), 0, columns - 1).astype(
            np.int32
        )
        return self.nearest[i, j], self.distance_km[i, j]


_cache: dict[tuple[tuple[int, ...], float], Coast] = {}


def coast(index: SegmentIndex, radius_km: float) -> Coast:
    cached = _cache.get((index.ids, radius_km))
    if cached is not None:
        return cached

    min_lon, min_lat, max_lon, max_lat = GRID.bbox.corners()
    rows = int((max_lat - min_lat) / RASTER_DEG) + 1
    columns = int((max_lon - min_lon) / RASTER_DEG) + 1
    nearest = np.full((rows, columns), -1, dtype=np.int32)
    distance = np.full((rows, columns), np.inf, dtype=np.float64)
    lat_axis = min_lat + np.arange(rows, dtype=np.float64) * RASTER_DEG
    lon_axis = min_lon + np.arange(columns, dtype=np.float64) * RASTER_DEG

    for position in range(len(index)):
        for lon, lat in index.geometry(position):
            scale = KM_PER_LON_DEG * math.cos(math.radians(lat))
            half_lat = radius_km / KM_PER_LAT_DEG
            half_lon = radius_km / max(1.0, scale)
            i0 = max(0, int((lat - half_lat - min_lat) / RASTER_DEG))
            i1 = min(rows, int((lat + half_lat - min_lat) / RASTER_DEG) + 2)
            j0 = max(0, int((lon - half_lon - min_lon) / RASTER_DEG))
            j1 = min(columns, int((lon + half_lon - min_lon) / RASTER_DEG) + 2)
            if i0 >= i1 or j0 >= j1:
                continue
            local = np.hypot(
                (lat_axis[i0:i1] - lat)[:, None] * KM_PER_LAT_DEG,
                (lon_axis[j0:j1] - lon)[None, :] * scale,
            )
            window = distance[i0:i1, j0:j1]
            closer = local < window
            window[closer] = local[closer]
            nearest[i0:i1, j0:j1][closer] = position

    built = Coast(segment_ids=index.ids, nearest=nearest, distance_km=distance)
    _cache[(index.ids, radius_km)] = built
    logger.info(
        "coast raster: %d segment(s), %dx%d cells, %.0f km reach",
        len(index),
        rows,
        columns,
        radius_km,
    )
    return built


def load_segments(session: Session) -> SegmentIndex:
    rows = list(
        session.execute(select(CoastalSegment).order_by(CoastalSegment.id)).scalars()
    )
    if not rows:
        raise RuntimeError("no coastal segments stored: run `strandarr reference`")
    return SegmentIndex.of(rows)
