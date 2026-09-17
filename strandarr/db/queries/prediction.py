from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from strandarr.analysis import drift
from strandarr.analysis import risk as risk_model
from strandarr.models import CoastalSegment, DriftDaily, SegmentRisk

SEGMENT_COLUMNS = (
    CoastalSegment.id,
    CoastalSegment.center_lat,
    CoastalSegment.center_lon,
    CoastalSegment.length_km,
    CoastalSegment.path,
)

DRIFT_WINDOW_DAYS = 3


def _joined(model: Any, columns: Sequence[Any], *where: Any) -> Select[Any]:
    return (
        select(*SEGMENT_COLUMNS, *columns)
        .join(model, model.coastal_segment_id == CoastalSegment.id)
        .where(*where)
    )


def drift_recent(session: Session, day: date) -> dict[int, float]:
    rows = session.execute(
        select(DriftDaily.coastal_segment_id, func.sum(DriftDaily.drift_index))
        .where(
            DriftDaily.day >= day - timedelta(days=DRIFT_WINDOW_DAYS),
            DriftDaily.day <= day,
            DriftDaily.model_version == drift.MODEL_VERSION,
        )
        .group_by(DriftDaily.coastal_segment_id)
    ).all()
    return {segment_id: float(value or 0.0) for segment_id, value in rows}


def risk(session: Session, day: date) -> Sequence[Any]:
    return session.execute(
        _joined(
            SegmentRisk,
            (
                SegmentRisk.probability,
                SegmentRisk.seasonal,
                SegmentRisk.drift_index,
                SegmentRisk.persistence,
                SegmentRisk.swell_m,
                SegmentRisk.onshore_m,
                SegmentRisk.source,
            ),
            SegmentRisk.day == day,
            SegmentRisk.model_version == risk_model.MODEL_VERSION,
        ).order_by(SegmentRisk.probability.desc())
    ).all()
