from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from strandarr.analysis import Float, Mask


@dataclass(frozen=True)
class Skill:
    days: int
    positives: int
    auc: float | None
    capture: dict[int, float]


TIE_TOLERANCE = 1e-9


def quantise(values: Float, tolerance: float = TIE_TOLERANCE) -> Float:
    largest = float(np.max(np.abs(values))) if values.size else 0.0
    if largest == 0.0:
        return values
    step = largest * tolerance
    return np.asarray(np.round(values / step) * step)


def mid_ranks(values: Float) -> Float:
    order = np.argsort(values, kind="stable")
    sorted_values = values[order]
    edges = np.flatnonzero(
        np.concatenate(([True], sorted_values[1:] != sorted_values[:-1], [True]))
    )
    means = (edges[:-1] + edges[1:] + 1) / 2.0
    ranks = np.empty(values.size, dtype=np.float64)
    ranks[order] = np.repeat(means, np.diff(edges))
    return ranks


def evaluate(pairs: Iterable[tuple[Float, Mask]], top_k: Sequence[int]) -> Skill:
    concordant = 0.0
    comparisons = 0.0
    hits = dict.fromkeys(top_k, 0)
    days = 0
    positives = 0

    for scores, positive in pairs:
        found = int(positive.sum())
        if not found:
            continue
        days += 1
        positives += found
        ranks = mid_ranks(quantise(scores))
        best = scores.size + 1 - ranks[positive]
        for k in top_k:
            hits[k] += int((best <= k).sum())
        absent = scores.size - found
        if absent:
            concordant += float(ranks[positive].sum()) - found * (found + 1) / 2.0
            comparisons += found * absent

    return Skill(
        days=days,
        positives=positives,
        auc=concordant / comparisons if comparisons else None,
        capture={k: hits[k] / positives if positives else 0.0 for k in top_k},
    )
