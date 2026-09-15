import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from strandarr import kinds
from strandarr.analysis import Float, Mask, coast, skill
from strandarr.analysis.drift import MAX_DRIFT_DAYS, MODEL_VERSION
from strandarr.models import DriftArrival, Stranding
from strandarr.services import coverage, reference

logger = logging.getLogger(__name__)

SNAP_RADIUS_KM = 15.0
TOP_K = (10, 25, 50)
ALL = "all"


@dataclass(frozen=True)
class Observation:
    day: date
    segment: int
    individuals: int
    precision: str


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


def _midnight(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=UTC)


def _observations(session: Session, snap: coast.Coast) -> tuple[list[Observation], int]:
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


def _model(session: Session, start: date, end: date) -> dict[date, dict[int, float]]:
    at = func.date(func.timezone("UTC", DriftArrival.arrival_at))
    rows = session.execute(
        select(at, DriftArrival.coastal_segment_id, func.sum(DriftArrival.drift_index))
        .where(
            DriftArrival.arrival_at >= _midnight(start),
            DriftArrival.arrival_at < _midnight(end) + timedelta(days=1),
            DriftArrival.model_version == MODEL_VERSION,
        )
        .group_by(at, DriftArrival.coastal_segment_id)
    ).all()
    scores: dict[date, dict[int, float]] = {}
    for day, segment_id, total in rows:
        scores.setdefault(day, {})[segment_id] = float(total)
    return scores


def _eligible(session: Session, start: date, end: date, minimum: int) -> set[date]:
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


def _climatology(
    observed: Sequence[Observation], segments: int
) -> Callable[[date], Float]:
    years = sorted({item.day.year for item in observed})
    slot = {year: position for position, year in enumerate(years)}
    counts = np.zeros((segments, 12, max(len(years), 1)), dtype=np.float64)
    for item in observed:
        counts[item.segment, item.day.month - 1, slot[item.day.year]] += 1.0
    totals = counts.sum(axis=2)

    def score(day: date) -> Float:
        column = totals[:, day.month - 1]
        position = slot.get(day.year)
        if position is None:
            return np.asarray(column)
        return np.asarray(column - counts[:, day.month - 1, position])

    return score


def _vector(scores: dict[int, float], index: dict[int, int], segments: int) -> Float:
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
            (
                (_vector(model.get(day, {}), index, segments), masks[day])
                for day in days
            ),
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
    snap = coast.build(segments, SNAP_RADIUS_KM)
    observed, unmatched = _observations(session, snap)
    model = _model(session, start, end)
    eligible = _eligible(session, start, end, min_release_days)
    logger.info(
        "validation: %d stranding(s) matched, %d beyond %.0f km, "
        "%d day(s) with at least %d simulated release day(s)",
        len(observed),
        unmatched,
        SNAP_RADIUS_KM,
        len(eligible),
        min_release_days,
    )
    baseline = _climatology(observed, len(segments))
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
