from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from strandarr.analysis import Float

HALF_LIFE_DAYS = 365.0

ISSUE_LAG_DAYS = 3


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


def build(observed: Iterable[tuple[int, date]]) -> Persistence:
    by_day: dict[date, list[int]] = {}
    for segment, day in observed:
        by_day.setdefault(day, []).append(segment)
    return Persistence(by_day=by_day)
