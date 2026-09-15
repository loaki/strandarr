import logging
from dataclasses import dataclass
from datetime import UTC, date

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from strandarr.analysis import coast
from strandarr.models import CoastalSegment, Stranding

logger = logging.getLogger(__name__)

SNAP_RADIUS_KM = 15.0


@dataclass(frozen=True)
class Observation:
    day: date
    segment: int
    individuals: int
    precision: str


def snapped(
    session: Session, segments: list[CoastalSegment]
) -> tuple[list[Observation], int]:
    rows = session.execute(
        select(
            Stranding.recorded_at,
            Stranding.lat,
            Stranding.lon,
            Stranding.individual_count,
            Stranding.location_precision,
        )
    ).all()
    if not rows:
        return [], 0
    snap = coast.build(segments, SNAP_RADIUS_KM)
    segment, distance = snap.lookup(
        np.array([row.lat for row in rows], dtype=np.float64),
        np.array([row.lon for row in rows], dtype=np.float64),
    )
    observed = [
        Observation(
            day=row.recorded_at.astimezone(UTC).date(),
            segment=int(index),
            individuals=row.individual_count,
            precision=row.location_precision or "unknown",
        )
        for row, index, km in zip(rows, segment, distance, strict=True)
        if index >= 0 and km <= SNAP_RADIUS_KM
    ]
    return observed, len(rows) - len(observed)
