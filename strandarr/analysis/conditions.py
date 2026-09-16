import numpy as np

from strandarr.analysis import Float
from strandarr.segments import SegmentIndex

LAG_DAYS = 2
REACH_KM = 25.0
KM_PER_DEG = 111.2


def weights(index: SegmentIndex, lats: Float, lons: Float) -> Float:
    scale = float(np.cos(np.radians(index.lats.mean()))) if len(index) else 1.0
    distance = np.hypot(
        (index.lats[:, None] - lats[None, :]) * KM_PER_DEG,
        (index.lons[:, None] - lons[None, :]) * KM_PER_DEG * scale,
    )
    kernel = np.exp(-0.5 * (distance / REACH_KM) ** 2)
    kernel[distance > 3.0 * REACH_KM] = 0.0
    totals = kernel.sum(axis=1, keepdims=True)
    return np.asarray(
        np.divide(kernel, totals, out=np.zeros_like(kernel), where=totals > 0.0)
    )


def onshore(index: SegmentIndex, east: Float, north: Float) -> Float:
    normal = np.radians(index.orientations + 90.0)
    return np.abs(east * np.sin(normal) + north * np.cos(normal))
