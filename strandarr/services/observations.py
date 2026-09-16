from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from strandarr.analysis import coast
from strandarr.db.queries import observation
from strandarr.segments import SegmentIndex
from strandarr.timeframe import day_of

SNAP_RADIUS_KM = 15.0

UNKNOWN_PRECISION = "unknown"


@dataclass(frozen=True)
class Observation:
    day: date
    segment: int
    individuals: int
    precision: str


def snap(
    rows: Sequence[Any], index: SegmentIndex, radius_km: float = SNAP_RADIUS_KM
) -> tuple[list[Observation], int]:
    if not rows:
        return [], 0
    raster = coast.build(index, radius_km)
    segment, distance = raster.lookup(
        np.array([row.lat for row in rows], dtype=np.float64),
        np.array([row.lon for row in rows], dtype=np.float64),
    )
    observed = [
        Observation(
            day=day_of(row.recorded_at),
            segment=int(position),
            individuals=row.individual_count,
            precision=row.location_precision or UNKNOWN_PRECISION,
        )
        for row, position, km in zip(rows, segment, distance, strict=True)
        if position >= 0 and km <= radius_km
    ]
    return observed, len(rows) - len(observed)


def load(session: Session, index: SegmentIndex) -> tuple[list[Observation], int]:
    return snap(observation.stranding_points(session), index)


def pairs(observed: Sequence[Observation]) -> list[tuple[int, date]]:
    return [(item.segment, item.day) for item in observed]
