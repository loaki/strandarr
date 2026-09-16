import logging
import math
from dataclasses import dataclass

import numpy as np

from strandarr.analysis import Float, Int
from strandarr.grid import GRID
from strandarr.segments import SegmentIndex

logger = logging.getLogger(__name__)

RASTER_DEG = 0.02
KM_PER_LAT_DEG = 110.6
KM_PER_LON_DEG = 111.3


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


def build(index: SegmentIndex, radius_km: float) -> Coast:
    key = (index.ids, radius_km)
    cached = _cache.get(key)
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

    coast = Coast(segment_ids=index.ids, nearest=nearest, distance_km=distance)
    _cache[key] = coast
    logger.info(
        "coast raster: %d segment(s), %dx%d cells, %.0f km reach",
        len(index),
        rows,
        columns,
        radius_km,
    )
    return coast
