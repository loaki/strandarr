import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
from sqlalchemy.orm import Session

from strandarr.analysis import Float, Mask, climatology
from strandarr.analysis.coast import SegmentIndex
from strandarr.analysis.drift import MAX_DRIFT_DAYS
from strandarr.analysis.timeframe import DayRange
from strandarr.db.queries import coverage, prediction, reference
from strandarr.services import observations
from strandarr.services.observations import Observation

logger = logging.getLogger(__name__)

TOP_K = (10, 25, 50)
ALL = "all"


@dataclass(frozen=True)
class Dataset:
    index: SegmentIndex
    observed: list[Observation]
    unmatched: int
    eligible: set[date]
    drift: dict[date, Float]
    climate: climatology.Climatology
    scored: dict[date, Mask]

    @property
    def size(self) -> int:
        return len(self.index)

    @property
    def days(self) -> list[date]:
        return sorted(self.scored)

    @property
    def labels(self) -> list[str]:
        return [ALL, *sorted({item.precision for item in self.observed})]

    def subset(self, label: str) -> list[Observation]:
        if label == ALL:
            return self.observed
        return [item for item in self.observed if item.precision == label]

    def masks(self, observed: Sequence[Observation] | None = None) -> dict[date, Mask]:
        rows = self.observed if observed is None else observed
        masks: dict[date, Mask] = {}
        for item in rows:
            if item.day not in self.eligible:
                continue
            mask = masks.setdefault(item.day, np.zeros(self.size, dtype=bool))
            mask[item.segment] = True
        return masks

    def baseline(self, day: date) -> Float:
        return self.climate.observed(day, without=day.year)

    @property
    def typical(self) -> Float:
        values = list(self.drift.values())
        return np.mean(values, axis=0) if values else self.index.blank()


def load(
    session: Session,
    days: DayRange,
    min_release_days: int = MAX_DRIFT_DAYS,
    margin_days: int = 0,
) -> Dataset:
    if not days:
        raise ValueError(f"start {days.start} is after end {days.end}")
    index = reference.segments(session)
    observed, unmatched = observations.load(session, index)
    raw = prediction.drift_totals(session, days.widened(margin_days))
    data = Dataset(
        index=index,
        observed=observed,
        unmatched=unmatched,
        eligible=coverage.eligible_days(session, days, min_release_days),
        drift={day: index.vector(scores) for day, scores in raw.items()},
        climate=climatology.build(observations.pairs(observed), len(index)),
        scored={},
    )
    data.scored.update(data.masks())
    logger.info(
        "dataset: %d stranding(s) matched, %d beyond %.0f km, %d scorable day(s), "
        "%d day(s) carrying drift",
        len(observed),
        unmatched,
        observations.SNAP_RADIUS_KM,
        len(data.eligible),
        len(data.drift),
    )
    return data
