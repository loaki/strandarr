import numpy as np

from strandarr.analysis import Float

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
