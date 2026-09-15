import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from strandarr import grid
from strandarr.analysis import Float, Int
from strandarr.models import CoastalSegment

logger = logging.getLogger(__name__)

RASTER_DEG = 0.02
KM_PER_LAT_DEG = 110.6
KM_PER_LON_DEG = 111.3


@dataclass(frozen=True)
class Coast:
    segment_ids: list[int]
    nearest: Int
    distance_km: Float

    def lookup(self, lat: Float, lon: Float) -> tuple[Int, Float]:
        min_lon, min_lat, _, _ = grid.BBOX
        rows, columns = self.nearest.shape
        i = np.clip(np.rint((lat - min_lat) / RASTER_DEG), 0, rows - 1).astype(np.int32)
        j = np.clip(np.rint((lon - min_lon) / RASTER_DEG), 0, columns - 1).astype(
            np.int32
        )
        return self.nearest[i, j], self.distance_km[i, j]


_cache: dict[float, tuple[tuple[int, ...], Coast]] = {}


def build(segments: Sequence[CoastalSegment], radius_km: float) -> Coast:
    key = tuple(segment.id for segment in segments)
    cached = _cache.get(radius_km)
    if cached is not None and cached[0] == key:
        return cached[1]

    min_lon, min_lat, max_lon, max_lat = grid.BBOX
    rows = int((max_lat - min_lat) / RASTER_DEG) + 1
    columns = int((max_lon - min_lon) / RASTER_DEG) + 1
    nearest = np.full((rows, columns), -1, dtype=np.int32)
    distance = np.full((rows, columns), np.inf, dtype=np.float64)
    lat_axis = min_lat + np.arange(rows, dtype=np.float64) * RASTER_DEG
    lon_axis = min_lon + np.arange(columns, dtype=np.float64) * RASTER_DEG

    for index, segment in enumerate(segments):
        for lon, lat in segment.path or [[segment.center_lon, segment.center_lat]]:
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
            nearest[i0:i1, j0:j1][closer] = index

    coast = Coast(
        segment_ids=[segment.id for segment in segments],
        nearest=nearest,
        distance_km=distance,
    )
    _cache[radius_km] = (key, coast)
    logger.info(
        "coast raster: %d segment(s), %dx%d cells, %.0f km reach",
        len(segments),
        rows,
        columns,
        radius_km,
    )
    return coast
