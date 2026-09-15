import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from strandarr import kinds
from strandarr.analysis import Float, Mask, climatology, skill
from strandarr.analysis.drift import MAX_DRIFT_DAYS, MODEL_VERSION
from strandarr.models import DriftDaily
from strandarr.services import coverage, observations, reference
from strandarr.services.observations import SNAP_RADIUS_KM, Observation

logger = logging.getLogger(__name__)

TOP_K = (10, 25, 50)
ALL = "all"

__all__ = [
    "SNAP_RADIUS_KM",
    "Observation",
    "Report",
    "Validation",
    "drift_scores",
    "eligible_days",
    "evaluate",
    "vector",
]


@dataclass(frozen=True)
class Report:
    label: str
    records: int
    individuals: int
    model: skill.Skill
    baseline: skill.Skill


@dataclass(frozen=True)
class Validation:
    start: date
    end: date
    segments: int
    matched: int
    unmatched: int
    eligible_days: int
    reports: list[Report]


def drift_scores(
    session: Session, start: date, end: date
) -> dict[date, dict[int, float]]:
    rows = session.execute(
        select(
            DriftDaily.day,
            DriftDaily.coastal_segment_id,
            func.sum(DriftDaily.drift_index),
        )
        .where(
            DriftDaily.day >= start,
            DriftDaily.day <= end,
            DriftDaily.model_version == MODEL_VERSION,
        )
        .group_by(DriftDaily.day, DriftDaily.coastal_segment_id)
    ).all()
    scores: dict[date, dict[int, float]] = {}
    for day, segment_id, total in rows:
        scores.setdefault(day, {})[segment_id] = float(total)
    return scores


def eligible_days(session: Session, start: date, end: date, minimum: int) -> set[date]:
    first = start - timedelta(days=MAX_DRIFT_DAYS - 1)
    simulated = coverage.covered_days(session, kinds.DRIFT_ARRIVALS, first, end)
    return {
        day
        for day in coverage.days(start, end)
        if sum(
            day - timedelta(days=offset) in simulated
            for offset in range(MAX_DRIFT_DAYS)
        )
        >= minimum
    }


def _baseline(
    observed: Sequence[Observation], segments: int
) -> Callable[[date], Float]:
    model = climatology.build(((item.segment, item.day) for item in observed), segments)
    return lambda day: model.observed(day, without=day.year)


def vector(scores: dict[int, float], index: dict[int, int], segments: int) -> Float:
    row = np.zeros(segments, dtype=np.float64)
    for segment_id, value in scores.items():
        position = index.get(segment_id)
        if position is not None:
            row[position] = value
    return row


def _report(
    label: str,
    observed: Sequence[Observation],
    eligible: set[date],
    model: dict[date, dict[int, float]],
    baseline: Callable[[date], Float],
    index: dict[int, int],
    segments: int,
) -> Report:
    positives: dict[date, list[int]] = {}
    records = 0
    individuals = 0
    for item in observed:
        if item.day not in eligible:
            continue
        positives.setdefault(item.day, []).append(item.segment)
        records += 1
        individuals += item.individuals

    days = sorted(positives)
    masks: dict[date, Mask] = {}
    for day in days:
        mask = np.zeros(segments, dtype=bool)
        mask[positives[day]] = True
        masks[day] = mask

    return Report(
        label=label,
        records=records,
        individuals=individuals,
        model=skill.evaluate(
            ((vector(model.get(day, {}), index, segments), masks[day]) for day in days),
            TOP_K,
        ),
        baseline=skill.evaluate(((baseline(day), masks[day]) for day in days), TOP_K),
    )


def evaluate(
    session: Session,
    start: date,
    end: date,
    min_release_days: int = MAX_DRIFT_DAYS,
) -> Validation:
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    segments = reference.segments(session)
    index = {segment.id: position for position, segment in enumerate(segments)}
    observed, unmatched = observations.snapped(session, segments)
    model = drift_scores(session, start, end)
    eligible = eligible_days(session, start, end, min_release_days)
    logger.info(
        "validation: %d stranding(s) matched, %d beyond %.0f km, "
        "%d day(s) with at least %d simulated release day(s)",
        len(observed),
        unmatched,
        SNAP_RADIUS_KM,
        len(eligible),
        min_release_days,
    )
    baseline = _baseline(observed, len(segments))
    return Validation(
        start=start,
        end=end,
        segments=len(segments),
        matched=len(observed),
        unmatched=unmatched,
        eligible_days=len(eligible),
        reports=[
            _report(
                label,
                [item for item in observed if label == ALL or item.precision == label],
                eligible,
                model,
                baseline,
                index,
                len(segments),
            )
            for label in [ALL, *sorted({item.precision for item in observed})]
        ],
    )
