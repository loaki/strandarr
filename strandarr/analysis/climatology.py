from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

import numpy as np

from strandarr.analysis import Float

MODEL_VERSION = "v1"

WINDOW_HALF_DAYS = 15
WINDOW_DAYS = 2 * WINDOW_HALF_DAYS + 1
SLOTS = 366

_SLOT_YEAR = 2000


def slot(day: date) -> int:
    return date(_SLOT_YEAR, day.month, day.day).timetuple().tm_yday - 1


def _windowed(counts: Float) -> Float:
    padded = np.concatenate(
        [counts[..., -WINDOW_HALF_DAYS:], counts, counts[..., :WINDOW_HALF_DAYS]],
        axis=-1,
    )
    cumulative = np.concatenate(
        [
            np.zeros((*counts.shape[:-1], 1), dtype=np.float64),
            np.cumsum(padded, axis=-1),
        ],
        axis=-1,
    )
    return np.asarray(cumulative[..., WINDOW_DAYS:] - cumulative[..., :-WINDOW_DAYS])


@dataclass(frozen=True)
class Climatology:
    total: Float
    per_year: dict[int, Float]
    years: tuple[int, ...]

    @property
    def rate(self) -> Float:
        if not self.years:
            return np.zeros_like(self.total)
        return np.asarray(self.total / (len(self.years) * WINDOW_DAYS))

    @property
    def chance(self) -> Float:
        return np.asarray(1.0 - np.exp(-self.rate))

    def observed(self, day: date, without: int | None = None) -> Float:
        column = self.total[:, slot(day)]
        if without is None or without not in self.per_year:
            return np.asarray(column)
        return np.asarray(column - self.per_year[without][:, slot(day)])


def build(observed: Iterable[tuple[int, date]], segments: int) -> Climatology:
    rows = list(observed)
    years = tuple(sorted({day.year for _, day in rows}))
    raw = {year: np.zeros((segments, SLOTS), dtype=np.float64) for year in years}
    for segment, day in rows:
        raw[day.year][segment, slot(day)] += 1.0
    per_year = {year: _windowed(counts) for year, counts in raw.items()}
    total = (
        np.sum(list(per_year.values()), axis=0)
        if per_year
        else np.zeros((segments, SLOTS), dtype=np.float64)
    )
    return Climatology(total=total, per_year=per_year, years=years)
