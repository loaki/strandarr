import numpy as np

from strandarr.analysis import Float, skill

MODEL_VERSION = "v1"

WEIGHTS: dict[str, float] = {
    "persistence": 2.4102,
    "drift": 0.9192,
    "swell": 0.1588,
    "onshore": -0.1413,
    "wave": 0.0727,
    "period": 0.0216,
}
INTERCEPT = -6.8343


def _ranked(values: Float) -> Float:
    if values.size == 0:
        return values
    return np.asarray(skill.mid_ranks(skill.quantise(values)) / values.size)


def predict(signals: dict[str, Float], segments: int) -> Float:
    score = np.full(segments, INTERCEPT, dtype=np.float64)
    for name, weight in WEIGHTS.items():
        values = signals.get(name)
        if values is None or not np.any(values):
            continue
        score = score + weight * _ranked(np.asarray(values, dtype=np.float64))
    return np.asarray(1.0 / (1.0 + np.exp(-score)))
