import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta

import numpy as np
from sqlalchemy.orm import Session

from strandarr.analysis import Float, Mask, climatology, skill
from strandarr.analysis.drift import MAX_DRIFT_DAYS
from strandarr.models import CoastalSegment
from strandarr.services import observations, reference, validation
from strandarr.services.observations import Observation

logger = logging.getLogger(__name__)

TOP_K = validation.TOP_K
MAX_WINDOW_DAYS = 5


NONE = "none"
SHUFFLED = "shuffled-segments"
OTHER_DAY = "other-day"
OTHER_YEAR = "same-day-other-year"
CONTROLS = (SHUFFLED, OTHER_DAY, OTHER_YEAR)


@dataclass(frozen=True)
class Variant:
    window_days: int = 0
    per_km: bool = False
    compress: str = "raw"
    blend: float = 0.0
    control: str = NONE

    @property
    def label(self) -> str:
        if self.blend >= 1.0:
            return "climatology"
        if self.control != NONE:
            return f"control: {self.control}"
        parts = [f"w{self.window_days}"]
        if self.per_km:
            parts.append("per-km")
        if self.compress != "raw":
            parts.append(self.compress)
        if self.blend > 0.0:
            parts.append(f"blend{self.blend:g}")
        return " ".join(parts)


@dataclass(frozen=True)
class Dataset:
    segments: list[CoastalSegment]
    lengths: Float
    drift: dict[date, Float]
    masks: dict[date, Mask]
    climate: climatology.Climatology
    observed: list[Observation]
    typical: Float

    @property
    def size(self) -> int:
        return len(self.segments)

    @property
    def days(self) -> list[date]:
        return sorted(self.masks)


def load(
    session: Session,
    start: date,
    end: date,
    min_release_days: int = MAX_DRIFT_DAYS,
) -> Dataset:
    segments = reference.segments(session)
    index = {segment.id: position for position, segment in enumerate(segments)}
    observed, unmatched = observations.snapped(session, segments)
    eligible = validation.eligible_days(session, start, end, min_release_days)

    margin = timedelta(days=MAX_WINDOW_DAYS)
    raw = validation.drift_scores(session, start - margin, end + margin)
    drift = {
        day: validation.vector(scores, index, len(segments))
        for day, scores in raw.items()
    }

    masks: dict[date, Mask] = {}
    for item in observed:
        if item.day not in eligible:
            continue
        mask = masks.setdefault(item.day, np.zeros(len(segments), dtype=bool))
        mask[item.segment] = True

    logger.info(
        "experiment: %d stranding(s) matched (%d dropped), %d scorable day(s), "
        "%d day(s) carrying drift",
        len(observed),
        unmatched,
        len(masks),
        len(drift),
    )
    return Dataset(
        segments=segments,
        lengths=np.array(
            [max(segment.length_km or 1.0, 0.1) for segment in segments],
            dtype=np.float64,
        ),
        drift=drift,
        masks=masks,
        climate=climatology.build(
            ((item.segment, item.day) for item in observed), len(segments)
        ),
        observed=observed,
        typical=(
            np.mean(list(drift.values()), axis=0)
            if drift
            else np.zeros(len(segments), dtype=np.float64)
        ),
    )


def _compress(values: Float, how: str) -> Float:
    if how == "sqrt":
        return np.sqrt(values)
    if how == "log":
        return np.log1p(values)
    if how == "rank":
        return skill.mid_ranks(values) / max(values.size, 1)
    return values


def _share(values: Float) -> Float:
    total = float(values.sum())
    return values / total if total > 0 else np.zeros_like(values)


def _source_day(data: Dataset, variant: Variant, day: date) -> date | None:
    rng = np.random.default_rng(day.toordinal())
    if variant.control == OTHER_DAY:
        pool = [other for other in data.days if other != day]
        return pool[int(rng.integers(len(pool)))] if pool else None
    if variant.control == OTHER_YEAR:
        pool = [
            other
            for other in data.drift
            if (other.month, other.day) == (day.month, day.day) and other != day
        ]
        return pool[int(rng.integers(len(pool)))] if pool else None
    return day


def score(data: Dataset, variant: Variant, day: date) -> Float:
    source = _source_day(data, variant, day)
    if source is None:
        return np.zeros(data.size, dtype=np.float64)
    window = [
        data.drift.get(source + timedelta(days=offset))
        for offset in range(-variant.window_days, variant.window_days + 1)
    ]
    present = [row for row in window if row is not None]
    values = (
        np.sum(present, axis=0) if present else np.zeros(data.size, dtype=np.float64)
    )

    if variant.control == SHUFFLED:
        rng = np.random.default_rng(day.toordinal())
        values = values[rng.permutation(values.size)]
    if variant.per_km:
        values = values / data.lengths
    if variant.blend <= 0.0:
        return values

    baseline = data.climate.observed(day, without=day.year)
    if variant.blend >= 1.0:
        return baseline
    return (1.0 - variant.blend) * _share(
        _compress(values, variant.compress)
    ) + variant.blend * _share(baseline)


def evaluate(data: Dataset, variant: Variant) -> skill.Skill:
    return skill.evaluate(
        ((score(data, variant, day), data.masks[day]) for day in data.days),
        TOP_K,
    )


def sparsity(data: Dataset) -> tuple[float, float, float]:
    counts = []
    hit = 0
    total = 0
    for day, mask in data.masks.items():
        values = data.drift.get(day)
        row = values if values is not None else np.zeros(data.size)
        counts.append(float((row > 0).sum()))
        total += int(mask.sum())
        hit += int((row[mask] > 0).sum())
    mean = float(np.mean(counts)) if counts else 0.0
    return mean, 100.0 * mean / max(data.size, 1), 100.0 * hit / max(total, 1)


def sweep(
    data: Dataset, variants: Sequence[Variant]
) -> list[tuple[Variant, skill.Skill]]:
    return [(variant, evaluate(data, variant)) for variant in variants]


def search(data: Dataset) -> list[tuple[str, list[tuple[Variant, skill.Skill]]]]:
    stages: list[tuple[str, list[tuple[Variant, skill.Skill]]]] = []
    best = Variant()

    plain = [best, *(replace(best, control=name) for name in CONTROLS)]
    stages.append(
        ("is there any day-specific signal? (controls want 0.500)", sweep(data, plain))
    )

    windows = [replace(best, window_days=n) for n in range(0, MAX_WINDOW_DAYS + 1)]
    stage = sweep(data, windows)
    stages.append(("sum drift over +/- N days", stage))
    best = _best(stage)

    lengths = [replace(best, per_km=flag) for flag in (False, True)]
    stage = sweep(data, lengths)
    stages.append(("divide by segment length", stage))
    best = _best(stage)

    blends = [
        replace(best, blend=w, compress=how)
        for how in ("raw", "sqrt", "log", "rank")
        for w in (0.2, 0.4, 0.6, 0.8)
    ]
    stage = sweep(data, [best, *blends, Variant(blend=1.0)])
    stages.append(("blend with climatology", stage))
    winner = _best(stage)

    controls = [replace(best, control=name) for name in CONTROLS]
    stage = sweep(data, [best, *controls])
    stages.append(("the same controls, against the tuned recipe", stage))
    stages.append(("winner", [(winner, evaluate(data, winner))]))
    return stages


def _best(stage: Sequence[tuple[Variant, skill.Skill]]) -> Variant:
    return max(stage, key=lambda row: row[1].auc or 0.0)[0]
