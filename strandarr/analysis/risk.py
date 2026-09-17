from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from strandarr.analysis import Float, climatology, skill
from strandarr.analysis.coast import SegmentIndex

MODEL_VERSION = "v2"

OBSERVED = "observed"
FORECAST = "forecast"

LAG_DAYS = 2
REACH_KM = 25.0
KM_PER_DEG = 111.2

HALF_LIFE_DAYS = 365.0
ISSUE_LAG_DAYS = 3

PRIOR_WEIGHT = 0.5
PRIOR_EVENTS = 0.5

SIGNAL_WEIGHTS: dict[str, float] = {
    "persistence": 2.4102,
    "drift": 0.9192,
    "swell": 0.1588,
    "onshore": -0.1413,
    "wave": 0.0727,
    "period": 0.0216,
}
INTERCEPT = -6.8343


def weights(index: SegmentIndex, lats: Float, lons: Float) -> Float:
    scale = float(np.cos(np.radians(index.lats.mean()))) if len(index) else 1.0
    distance = np.hypot(
        (index.lats[:, None] - lats[None, :]) * KM_PER_DEG,
        (index.lons[:, None] - lons[None, :]) * KM_PER_DEG * scale,
    )
    kernel = np.exp(-0.5 * (distance / REACH_KM) ** 2)
    kernel[distance > 3.0 * REACH_KM] = 0.0
    totals = kernel.sum(axis=1, keepdims=True)
    return np.asarray(
        np.divide(kernel, totals, out=np.zeros_like(kernel), where=totals > 0.0)
    )


def onshore(index: SegmentIndex, east: Float, north: Float) -> Float:
    normal = np.radians(index.orientations + 90.0)
    return np.abs(east * np.sin(normal) + north * np.cos(normal))


@dataclass(frozen=True)
class Persistence:
    by_day: dict[date, list[int]]

    def known_at(self, day: date, lag_days: int = ISSUE_LAG_DAYS) -> date:
        return day - timedelta(days=lag_days)

    def score(self, day: date, segments: int, lag_days: int = ISSUE_LAG_DAYS) -> Float:
        cutoff = self.known_at(day, lag_days)
        decay = 0.5 ** (1.0 / HALF_LIFE_DAYS)
        total = np.zeros(segments, dtype=np.float64)
        for seen, positions in self.by_day.items():
            if seen >= cutoff:
                continue
            weight = decay ** (cutoff - seen).days
            for position in positions:
                if 0 <= position < segments:
                    total[position] += weight
        return total


def persistence(observed: Iterable[tuple[int, date]]) -> Persistence:
    by_day: dict[date, list[int]] = {}
    for segment, day in observed:
        by_day.setdefault(day, []).append(segment)
    return Persistence(by_day=by_day)


def logit(chance: Float) -> Float:
    return np.asarray(np.log(chance / (1.0 - chance)))


def _chance(counts: Float, years: int) -> Float:
    rate = (counts + PRIOR_EVENTS) / (max(years, 1) * climatology.WINDOW_DAYS)
    return np.asarray(1.0 - np.exp(-rate))


def seasonal(model: climatology.Climatology, day: date, segments: int) -> Float:
    if not model.years:
        return _chance(np.zeros(segments, dtype=np.float64), 1)
    held_out = day.year in model.per_year
    years = len(model.years) - (1 if held_out else 0)
    return _chance(model.observed(day, without=day.year), years)


def reference(model: climatology.Climatology, segments: int) -> float:
    if not model.years:
        return float(logit(_chance(np.zeros(segments, dtype=np.float64), 1))[0])
    return float(np.mean(logit(_chance(model.total, len(model.years)))))


def _ranked(values: Float) -> Float:
    if values.size == 0:
        return values
    return np.asarray(skill.mid_ranks(skill.quantise(values)) / values.size)


def predict(signals: dict[str, Float], offset: Float, segments: int) -> Float:
    score = np.full(segments, INTERCEPT, dtype=np.float64) + PRIOR_WEIGHT * offset
    for name, weight in SIGNAL_WEIGHTS.items():
        values = signals.get(name)
        row = (
            np.zeros(segments, dtype=np.float64)
            if values is None
            else np.asarray(values, dtype=np.float64)
        )
        score = score + weight * _ranked(row)
    return np.asarray(1.0 / (1.0 + np.exp(-score)))
