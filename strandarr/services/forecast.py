import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import numpy as np
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from strandarr import sources
from strandarr.analysis import conditions, persistence
from strandarr.analysis.drift import MODEL_VERSION as DRIFT_VERSION
from strandarr.analysis.forecast import MODEL_VERSION, predict
from strandarr.models import (
    CoastalSegment,
    DriftDaily,
    MarineCondition,
    SegmentForecast,
)
from strandarr.repositories import store
from strandarr.services import coverage, observations, reference

logger = logging.getLogger(__name__)

DRIFT_WINDOW_DAYS = 3


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def _sea_state(
    session: Session, segments: list[CoastalSegment], day: date
) -> dict[str, np.ndarray]:
    seen = day - timedelta(days=conditions.LAG_DAYS)
    wave = MarineCondition.wave_height_m
    heading = func.radians(MarineCondition.wave_direction_deg + 180.0)
    rows = session.execute(
        select(
            MarineCondition.lat,
            MarineCondition.lon,
            func.avg(MarineCondition.swell_height_m),
            func.avg(wave),
            func.avg(MarineCondition.wave_period_s),
            func.avg(wave * func.sin(heading)),
            func.avg(wave * func.cos(heading)),
        )
        .where(
            MarineCondition.valid_at >= _midnight(seen),
            MarineCondition.valid_at < _midnight(seen) + timedelta(days=1),
            MarineCondition.source.in_(sources.OBSERVED_BEFORE_PREDICTED),
        )
        .group_by(MarineCondition.lat, MarineCondition.lon)
    ).all()
    if not rows:
        return {}

    weights = conditions.weights(
        segments,
        np.array([row[0] for row in rows], dtype=np.float64),
        np.array([row[1] for row in rows], dtype=np.float64),
    )

    def resolve(column: int) -> np.ndarray:
        values = np.array([float(row[column] or 0.0) for row in rows], dtype=np.float64)
        return np.asarray(weights @ values)

    return {
        "swell": resolve(2),
        "wave": resolve(3),
        "period": resolve(4),
        "onshore": conditions.onshore(segments, resolve(5), resolve(6)),
    }


def _drift(
    session: Session, day: date, index: dict[int, int], count: int
) -> np.ndarray:
    rows = session.execute(
        select(DriftDaily.coastal_segment_id, func.sum(DriftDaily.drift_index))
        .where(
            DriftDaily.day >= day - timedelta(days=DRIFT_WINDOW_DAYS),
            DriftDaily.day <= day,
            DriftDaily.model_version == DRIFT_VERSION,
        )
        .group_by(DriftDaily.coastal_segment_id)
    ).all()
    values = np.zeros(count, dtype=np.float64)
    for segment_id, total in rows:
        position = index.get(segment_id)
        if position is not None:
            values[position] = float(total or 0.0)
    return values


def build(session: Session, kind: str, payload: dict[str, Any]) -> int:
    start = date.fromisoformat(payload["start"])
    end = date.fromisoformat(payload["end"])
    segments = reference.segments(session)
    index = {segment.id: position for position, segment in enumerate(segments)}
    observed, _ = observations.snapped(session, segments)
    recent = persistence.build((item.segment, item.day) for item in observed)

    total = 0
    for day in coverage.days(start, end):
        signals = _sea_state(session, segments, day)
        complete = bool(signals)
        signals["persistence"] = recent.score(day, len(segments))
        signals["drift"] = _drift(session, day, index, len(segments))
        probability = predict(signals, len(segments))
        blank = np.zeros(len(segments), dtype=np.float64)

        session.execute(
            delete(SegmentForecast).where(
                SegmentForecast.day == day,
                SegmentForecast.model_version == MODEL_VERSION,
            )
        )
        total += store.upsert(
            session,
            SegmentForecast,
            [
                SegmentForecast(
                    day=day,
                    coastal_segment_id=segment.id,
                    model_version=MODEL_VERSION,
                    probability=float(probability[position]),
                    persistence=float(signals["persistence"][position]),
                    drift_index=float(signals["drift"][position]),
                    swell_m=float(signals.get("swell", blank)[position]),
                    onshore_m=float(signals.get("onshore", blank)[position]),
                )
                for position, segment in enumerate(segments)
            ],
            overwrite=True,
        )
        coverage.record(session, kind, {day: len(segments)}, complete=complete)
        session.commit()

    logger.info("forecast: %d segment-day(s) for %s..%s", total, start, end)
    return total
