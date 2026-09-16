from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from strandarr.analysis import skill
from strandarr.analysis.drift import MAX_DRIFT_DAYS
from strandarr.services.dataset import TOP_K, Dataset, load
from strandarr.services.observations import SNAP_RADIUS_KM, Observation
from strandarr.timeframe import DayRange

__all__ = ["SNAP_RADIUS_KM", "TOP_K", "Report", "Validation", "evaluate"]


@dataclass(frozen=True)
class Report:
    label: str
    records: int
    individuals: int
    model: skill.Skill
    baseline: skill.Skill


@dataclass(frozen=True)
class Validation:
    days: DayRange
    segments: int
    matched: int
    unmatched: int
    eligible_days: int
    reports: list[Report]


def _report(data: Dataset, label: str, observed: Sequence[Observation]) -> Report:
    masks = data.masks(observed)
    days = sorted(masks)
    scored = [item for item in observed if item.day in data.eligible]
    return Report(
        label=label,
        records=len(scored),
        individuals=sum(item.individuals for item in scored),
        model=skill.evaluate(
            ((data.drift.get(day, data.index.blank()), masks[day]) for day in days),
            TOP_K,
        ),
        baseline=skill.evaluate(
            ((data.baseline(day), masks[day]) for day in days), TOP_K
        ),
    )


def evaluate(
    session: Session,
    start: date,
    end: date,
    min_release_days: int = MAX_DRIFT_DAYS,
) -> Validation:
    days = DayRange(start, end)
    data = load(session, days, min_release_days)
    return Validation(
        days=days,
        segments=data.size,
        matched=len(data.observed),
        unmatched=data.unmatched,
        eligible_days=len(data.eligible),
        reports=[_report(data, label, data.subset(label)) for label in data.labels],
    )
