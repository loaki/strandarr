from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta

import numpy as np
from sqlalchemy.orm import Session

from strandarr.analysis import Float, skill
from strandarr.analysis.drift import MAX_DRIFT_DAYS
from strandarr.analysis.timeframe import DayRange
from strandarr.services.dataset import TOP_K, Dataset, load

MAX_WINDOW_DAYS = 5

NONE = "none"
SHUFFLED = "shuffled-segments"
OTHER_DAY = "other-day"
OTHER_YEAR = "same-day-other-year"
CONTROLS = (SHUFFLED, OTHER_DAY, OTHER_YEAR)

COMPRESSIONS = ("raw", "sqrt", "log", "rank")
BLENDS = (0.2, 0.4, 0.6, 0.8)

Stage = list[tuple["Variant", skill.Skill]]


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


def dataset(
    session: Session,
    start: date,
    end: date,
    min_release_days: int = MAX_DRIFT_DAYS,
) -> Dataset:
    return load(
        session, DayRange(start, end), min_release_days, margin_days=MAX_WINDOW_DAYS
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
        return data.index.blank()
    present = [
        row
        for row in (
            data.drift.get(source + timedelta(days=offset))
            for offset in range(-variant.window_days, variant.window_days + 1)
        )
        if row is not None
    ]
    values = np.sum(present, axis=0) if present else data.index.blank()

    if variant.control == SHUFFLED:
        rng = np.random.default_rng(day.toordinal())
        values = values[rng.permutation(values.size)]
    if variant.per_km:
        values = values / data.index.divisors
    if variant.blend <= 0.0:
        return values

    baseline = data.baseline(day)
    if variant.blend >= 1.0:
        return baseline
    return (1.0 - variant.blend) * _share(
        _compress(values, variant.compress)
    ) + variant.blend * _share(baseline)


def evaluate(data: Dataset, variant: Variant) -> skill.Skill:
    return skill.evaluate(
        ((score(data, variant, day), data.scored[day]) for day in data.days), TOP_K
    )


def sparsity(data: Dataset) -> tuple[float, float, float]:
    counts = []
    hit = 0
    total = 0
    for day, mask in data.scored.items():
        row = data.drift.get(day, data.index.blank())
        counts.append(float((row > 0).sum()))
        total += int(mask.sum())
        hit += int((row[mask] > 0).sum())
    mean = float(np.mean(counts)) if counts else 0.0
    return mean, 100.0 * mean / max(data.size, 1), 100.0 * hit / max(total, 1)


def sweep(data: Dataset, variants: Sequence[Variant]) -> Stage:
    return [(variant, evaluate(data, variant)) for variant in variants]


def _best(stage: Stage) -> Variant:
    return max(stage, key=lambda row: row[1].auc or 0.0)[0]


def search(data: Dataset) -> list[tuple[str, Stage]]:
    stages: list[tuple[str, Stage]] = []
    best = Variant()

    plain = [best, *(replace(best, control=name) for name in CONTROLS)]
    stages.append(
        ("is there any day-specific signal? (controls want 0.500)", sweep(data, plain))
    )

    stage = sweep(
        data, [replace(best, window_days=n) for n in range(MAX_WINDOW_DAYS + 1)]
    )
    stages.append(("sum drift over +/- N days", stage))
    best = _best(stage)

    stage = sweep(data, [replace(best, per_km=flag) for flag in (False, True)])
    stages.append(("divide by segment length", stage))
    best = _best(stage)

    blends = [
        replace(best, blend=weight, compress=how)
        for how in COMPRESSIONS
        for weight in BLENDS
    ]
    stage = sweep(data, [best, *blends, Variant(blend=1.0)])
    stages.append(("blend with climatology", stage))
    winner = _best(stage)

    stages.append(
        (
            "the same controls, against the tuned recipe",
            sweep(data, [best, *(replace(best, control=n) for n in CONTROLS)]),
        )
    )
    stages.append(("winner", [(winner, evaluate(data, winner))]))
    return stages
