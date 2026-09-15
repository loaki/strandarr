from collections.abc import Sequence

import numpy as np

from strandarr.analysis import Float
from strandarr.models import CoastalSegment

LAG_DAYS = 2
REACH_KM = 25.0
KM_PER_DEG = 111.2


def weights(segments: Sequence[CoastalSegment], lats: Float, lons: Float) -> Float:
    slat = np.array([s.center_lat for s in segments], dtype=np.float64)
    slon = np.array([s.center_lon for s in segments], dtype=np.float64)
    scale = float(np.cos(np.radians(slat.mean()))) if slat.size else 1.0
    distance = np.hypot(
        (slat[:, None] - lats[None, :]) * KM_PER_DEG,
        (slon[:, None] - lons[None, :]) * KM_PER_DEG * scale,
    )
    kernel = np.exp(-0.5 * (distance / REACH_KM) ** 2)
    kernel[distance > 3.0 * REACH_KM] = 0.0
    totals = kernel.sum(axis=1, keepdims=True)
    return np.asarray(
        np.divide(kernel, totals, out=np.zeros_like(kernel), where=totals > 0.0)
    )


def onshore(segments: Sequence[CoastalSegment], east: Float, north: Float) -> Float:
    normal = np.radians(
        np.array(
            [(s.coastline_orientation_deg or 0.0) + 90.0 for s in segments],
            dtype=np.float64,
        )
    )
    return np.abs(east * np.sin(normal) + north * np.cos(normal))
