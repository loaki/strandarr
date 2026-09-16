from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from strandarr.analysis import climatology as climatology_model
from strandarr.analysis import drift
from strandarr.analysis import forecast as forecast_model
from strandarr.models import (
    CoastalSegment,
    DriftDaily,
    SegmentClimatology,
    SegmentForecast,
)
from strandarr.timeframe import DayRange

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


def drift_totals(session: Session, days: DayRange) -> dict[date, dict[int, float]]:
    rows = session.execute(
        select(
            DriftDaily.day,
            DriftDaily.coastal_segment_id,
            func.sum(DriftDaily.drift_index),
        )
        .where(
            DriftDaily.day >= days.start,
            DriftDaily.day <= days.end,
            DriftDaily.model_version == drift.MODEL_VERSION,
        )
        .group_by(DriftDaily.day, DriftDaily.coastal_segment_id)
    ).all()
    totals: dict[date, dict[int, float]] = {}
    for day, segment_id, value in rows:
        totals.setdefault(day, {})[segment_id] = float(value)
    return totals


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
    total = func.sum(DriftDaily.drift_index)
    return session.execute(
        _joined(
            DriftDaily,
            (total.label("drift_index"), func.count().label("release_days")),
            DriftDaily.day == day,
            DriftDaily.model_version == drift.MODEL_VERSION,
        )
        .group_by(CoastalSegment.id)
        .order_by(total.desc())
    ).all()


def climatology(session: Session, day_of_year: int) -> Sequence[Any]:
    return session.execute(
        _joined(
            SegmentClimatology,
            (
                SegmentClimatology.probability,
                SegmentClimatology.expected_per_day,
                SegmentClimatology.observed,
                SegmentClimatology.years,
            ),
            SegmentClimatology.day_of_year == day_of_year,
            SegmentClimatology.model_version == climatology_model.MODEL_VERSION,
        ).order_by(SegmentClimatology.probability.desc())
    ).all()


def forecast(session: Session, day: date) -> Sequence[Any]:
    return session.execute(
        _joined(
            SegmentForecast,
            (
                SegmentForecast.probability,
                SegmentForecast.persistence,
                SegmentForecast.drift_index,
                SegmentForecast.swell_m,
                SegmentForecast.onshore_m,
            ),
            SegmentForecast.day == day,
            SegmentForecast.model_version == forecast_model.MODEL_VERSION,
        ).order_by(SegmentForecast.probability.desc())
    ).all()
