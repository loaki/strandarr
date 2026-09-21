import json
import logging
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from strandarr.analysis import Float
from strandarr.analysis.drift import MAX_DRIFT_DAYS
from strandarr.analysis.geo import SegmentIndex, coast, load_segments
from strandarr.config import settings
from strandarr.db import replace
from strandarr.errors import NotReady
from strandarr.models import Condition, DriftDaily, SegmentRisk, Stranding
from strandarr.timeframe import DayRange, day_of

logger = logging.getLogger(__name__)

OBSERVED = "observed"
FORECAST = "forecast"

SIGNALS = SegmentRisk.SIGNALS

LAG_DAYS = 2
ISSUE_LAG_DAYS = 3
DRIFT_WINDOW_DAYS = 3

REACH_KM = 25.0
KM_PER_DEG = 111.2
SNAP_RADIUS_KM = 15.0

HALF_LIFE_DAYS = 365.0

WINDOW_HALF_DAYS = 15
WINDOW_DAYS = 2 * WINDOW_HALF_DAYS + 1
SLOTS = 366
_SLOT_YEAR = 2000
PRIOR_EVENTS = 0.5

PACKAGED_COEFFICIENTS = Path(__file__).resolve().parent.parent / "coefficients.json"
COEFFICIENTS_PATH = (
    Path(settings.coefficients_path)
    if settings.coefficients_path
    else PACKAGED_COEFFICIENTS
)


@dataclass(frozen=True)
class Model:
    intercept: float
    weights: dict[str, float]
    scales: dict[str, float]
    seasonal_weight: float
    seasonal_baseline: float
    fitted: bool

    @classmethod
    def load(cls) -> "Model":
        # A fit written to a volume wins; the packaged file is the starting point.
        path = (
            COEFFICIENTS_PATH if COEFFICIENTS_PATH.exists() else PACKAGED_COEFFICIENTS
        )
        raw = json.loads(path.read_text())
        return cls(
            intercept=float(raw["intercept"]),
            weights={name: float(raw["weights"][name]) for name in SIGNALS},
            scales={name: float(raw["scales"][name]) for name in SIGNALS},
            seasonal_weight=float(raw["seasonal_weight"]),
            seasonal_baseline=float(raw["seasonal_baseline"]),
            fitted=bool(raw.get("fitted", False)),
        )

    def dump(self) -> dict[str, Any]:
        return {
            "intercept": self.intercept,
            "weights": self.weights,
            "scales": self.scales,
            "seasonal_weight": self.seasonal_weight,
            "seasonal_baseline": self.seasonal_baseline,
            "fitted": self.fitted,
        }

    def save(self) -> None:
        COEFFICIENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        COEFFICIENTS_PATH.write_text(json.dumps(self.dump(), indent=2) + "\n")


def transform(values: Float, scale: float) -> Float:
    return np.asarray(np.log1p(np.clip(values, 0.0, None) / max(scale, 1e-9)))


def design(signals: dict[str, Float], seasonal: Float, model: Model) -> Float:
    columns = [transform(signals[name], model.scales[name]) for name in SIGNALS]
    columns.append(logit(seasonal) - model.seasonal_baseline)
    return np.asarray(np.column_stack(columns))


def predict(matrix: Float, model: Model) -> Float:
    beta = np.array([*(model.weights[name] for name in SIGNALS), model.seasonal_weight])
    return np.asarray(1.0 / (1.0 + np.exp(-(model.intercept + matrix @ beta))))


def logit(chance: Float) -> Float:
    safe = np.clip(chance, 1e-9, 1.0 - 1e-9)
    return np.asarray(np.log(safe / (1.0 - safe)))


INDEX_QUANTILES = 101
INDEX_CACHE_SECONDS = 600

_scale: tuple[float, Float, Float] | None = None


def index_scale(session: Session) -> tuple[Float, Float]:
    """Breakpoints mapping the stored probabilities onto 0-100.

    Measured from segment_risk rather than pinned to the coefficients, so the
    scale describes the model that actually produced the numbers on screen and
    cannot go stale behind a refit. It is the distribution over every stored
    segment-day, not over the day being drawn: that is what makes the same
    colour mean the same absolute chance on a calm day and a stormy one.
    """
    global _scale
    now = time.monotonic()
    if _scale is not None and now - _scale[0] < INDEX_CACHE_SECONDS:
        return _scale[1], _scale[2]

    shares = np.linspace(0.0, 1.0, INDEX_QUANTILES)
    measured = session.execute(
        select(
            func.percentile_cont(shares.tolist()).within_group(
                SegmentRisk.probability.asc()
            )
        )
    ).scalar()

    breaks = np.asarray(measured or [], dtype=np.float64)
    levels = 100.0 * shares
    if len(breaks) == len(shares):
        # Flat stretches would repeat a breakpoint and make np.interp pick
        # arbitrarily between levels, so drop duplicates instead of nudging them.
        keep = np.concatenate([np.diff(breaks) > 0.0, [True]])
        breaks, levels = breaks[keep], levels[keep]
    else:
        breaks, levels = np.asarray([]), np.asarray([])

    _scale = (now, breaks, levels)
    return breaks, levels


def index_of(probability: Float, breaks: Float, levels: Float) -> Float:
    """Rescale a probability to 0-100 for the map.

    Monotone, so the index can never disagree with the model, and shared across
    days, so the colour means the same thing whenever you look at it.
    """
    values = np.asarray(probability, dtype=np.float64)
    if len(breaks) < 2:
        return np.zeros_like(values)
    return np.asarray(np.interp(values, breaks, levels))


@dataclass(frozen=True)
class Observed:
    pairs: tuple[tuple[int, date], ...]

    def by_day(self) -> dict[date, list[int]]:
        grouped: dict[date, list[int]] = {}
        for segment, day in self.pairs:
            grouped.setdefault(day, []).append(segment)
        return grouped


def snap(session: Session, index: SegmentIndex) -> Observed:
    rows = session.execute(
        select(Stranding.recorded_at, Stranding.lat, Stranding.lon)
    ).all()
    if not rows:
        return Observed(pairs=())
    raster = coast(index, SNAP_RADIUS_KM)
    segment, distance = raster.lookup(
        np.array([row[1] for row in rows], dtype=np.float64),
        np.array([row[2] for row in rows], dtype=np.float64),
    )
    return Observed(
        pairs=tuple(
            (int(position), day_of(row[0]))
            for row, position, km in zip(rows, segment, distance, strict=True)
            if position >= 0 and km <= SNAP_RADIUS_KM
        )
    )


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

    def observed(self, day: date, without: int | None = None) -> Float:
        column = self.total[:, slot(day)]
        if without is None or without not in self.per_year:
            return np.asarray(column)
        return np.asarray(column - self.per_year[without][:, slot(day)])

    def chance(self, day: date, segments: int) -> Float:
        if not self.years:
            return _chance(np.zeros(segments, dtype=np.float64), 1)
        held_out = day.year in self.per_year
        return _chance(
            self.observed(day, without=day.year),
            len(self.years) - (1 if held_out else 0),
        )

    def baseline(self, segments: int) -> float:
        if not self.years:
            return float(logit(_chance(np.zeros(segments, dtype=np.float64), 1))[0])
        return float(np.mean(logit(_chance(self.total, len(self.years)))))


def _chance(counts: Float, years: int) -> Float:
    rate = (counts + PRIOR_EVENTS) / (max(years, 1) * WINDOW_DAYS)
    return np.asarray(1.0 - np.exp(-rate))


def climatology(observed: Iterable[tuple[int, date]], segments: int) -> Climatology:
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


def persistence(observed: Observed, day: date, segments: int) -> Float:
    cutoff = day - timedelta(days=ISSUE_LAG_DAYS)
    decay = 0.5 ** (1.0 / HALF_LIFE_DAYS)
    total = np.zeros(segments, dtype=np.float64)
    for seen, positions in observed.by_day().items():
        if seen >= cutoff:
            continue
        weight = decay ** (cutoff - seen).days
        for position in positions:
            if 0 <= position < segments:
                total[position] += weight
    return total


def spatial_weights(index: SegmentIndex, lats: Float, lons: Float) -> Float:
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


def sea_state(session: Session, index: SegmentIndex, day: date) -> dict[str, Float]:
    seen = day - timedelta(days=LAG_DAYS)
    start, end = DayRange.of(seen).bounds()
    wave = Condition.wave_height_m
    heading = func.radians(Condition.wave_direction_deg + 180.0)
    rows = session.execute(
        select(
            Condition.lat,
            Condition.lon,
            func.avg(Condition.swell_height_m),
            func.avg(wave),
            func.avg(Condition.wave_period_s),
            func.avg(wave * func.sin(heading)),
            func.avg(wave * func.cos(heading)),
        )
        .where(Condition.valid_at >= start, Condition.valid_at < end)
        .group_by(Condition.lat, Condition.lon)
    ).all()
    if not rows:
        raise NotReady(f"no conditions stored for {seen}, needed to score {day}")

    kernel = spatial_weights(
        index,
        np.array([row[0] for row in rows], dtype=np.float64),
        np.array([row[1] for row in rows], dtype=np.float64),
    )

    def resolve(column: int) -> Float:
        values = np.array([float(row[column] or 0.0) for row in rows], dtype=np.float64)
        return np.asarray(kernel @ values)

    return {
        "swell_m": resolve(2),
        "wave_m": resolve(3),
        "period_s": resolve(4),
        "onshore_m": onshore(index, resolve(5), resolve(6)),
    }


def drift_recent(session: Session, index: SegmentIndex, day: date) -> Float:
    rows = session.execute(
        select(DriftDaily.coastal_segment_id, func.sum(DriftDaily.drift_index))
        .where(
            DriftDaily.day >= day - timedelta(days=DRIFT_WINDOW_DAYS),
            DriftDaily.day <= day,
        )
        .group_by(DriftDaily.coastal_segment_id)
    ).all()
    return index.vector({segment: float(value or 0.0) for segment, value in rows})


@dataclass(frozen=True)
class History:
    observed: Observed
    climatology: Climatology
    baseline: float


_history: dict[Any, History] = {}


def history(session: Session, index: SegmentIndex) -> History:
    counted, newest = session.execute(
        select(func.count(), func.max(Stranding.id))
    ).one()
    fingerprint = (counted, newest, len(index))
    cached = _history.get(fingerprint)
    if cached is not None:
        return cached
    observed = snap(session, index)
    model = climatology(observed.pairs, len(index))
    built = History(
        observed=observed, climatology=model, baseline=model.baseline(len(index))
    )
    _history.clear()
    _history[fingerprint] = built
    logger.info(
        "risk: %d stranding(s) snapped to segments over %d year(s)",
        len(observed.pairs),
        len(model.years),
    )
    return built


def signals(session: Session, index: SegmentIndex, day: date) -> dict[str, Float]:
    past = history(session, index)
    return {
        **sea_state(session, index, day),
        "persistence": persistence(past.observed, day, len(index)),
        "drift_index": drift_recent(session, index, day),
    }


def run(session: Session, day: date) -> int:
    index = load_segments(session)
    past = history(session, index)
    model = Model.load()
    if not model.fitted:
        model = Model(**{**model.dump(), "seasonal_baseline": past.baseline})

    raw = signals(session, index, day)
    seasonal = past.climatology.chance(day, len(index))
    if model.fitted:
        probability = predict(design(raw, seasonal, model), model)
    else:
        # The shipped weights were fitted against per-day ranks in [0, 1] and are
        # meaningless applied to log1p(signal / scale) -- persistence alone can
        # cancel the intercept and push the probability into the tens of percent.
        # Until `strandarr fit` has run, report the seasonal climatology, which is
        # measured from the stranding record, and store the signals so fit has
        # something to learn from.
        probability = seasonal
    source = FORECAST if day > date.today() - timedelta(days=LAG_DAYS) else OBSERVED

    written = replace(
        session,
        SegmentRisk,
        [
            SegmentRisk(
                day=day,
                coastal_segment_id=segment_id,
                source=source,
                probability=float(probability[position]),
                seasonal=float(seasonal[position]),
                **{name: float(raw[name][position]) for name in SIGNALS},
            )
            for position, segment_id in enumerate(index.ids)
        ],
        SegmentRisk.day == day,
    )
    session.commit()
    logger.info(
        "risk %s: %d segment(s), peak %.4f%s",
        day,
        written,
        float(probability.max()) if written else 0.0,
        ""
        if model.fitted
        else " (seasonal climatology only; run `strandarr fit` to use the signals)",
    )
    return written


NEGATIVES_PER_POSITIVE = 20
RIDGE = 1e-3
MAX_ITERATIONS = 50
TOLERANCE = 1e-8


def irls(matrix: Float, target: Float) -> Float:
    design_matrix = np.column_stack([np.ones(len(matrix)), matrix])
    beta = np.zeros(design_matrix.shape[1])
    penalty = RIDGE * np.eye(design_matrix.shape[1])
    penalty[0, 0] = 0.0
    for _ in range(MAX_ITERATIONS):
        eta = design_matrix @ beta
        chance = 1.0 / (1.0 + np.exp(-eta))
        weight = np.clip(chance * (1.0 - chance), 1e-6, None)
        working = eta + (target - chance) / weight
        weighted = design_matrix.T * weight
        step = np.linalg.solve(weighted @ design_matrix + penalty, weighted @ working)
        if np.max(np.abs(step - beta)) < TOLERANCE:
            return np.asarray(step)
        beta = step
    return np.asarray(beta)


def fit(session: Session, seed: int = 0) -> Model:
    index = load_segments(session)
    past = history(session, index)
    stored = session.execute(
        select(
            SegmentRisk.day,
            SegmentRisk.coastal_segment_id,
            SegmentRisk.seasonal,
            *(getattr(SegmentRisk, name) for name in SIGNALS),
        ).order_by(SegmentRisk.day)
    ).all()
    if not stored:
        raise RuntimeError("no segment_risk rows stored: run the risk job first")

    positions = {segment_id: i for i, segment_id in enumerate(index.ids)}
    struck = set(past.observed.pairs)
    target = np.array(
        [
            1.0 if (positions.get(row[1], -1), row[0]) in struck else 0.0
            for row in stored
        ]
    )
    values = np.array([[float(v) for v in row[3:]] for row in stored], dtype=np.float64)
    seasonal = np.array([float(row[2]) for row in stored], dtype=np.float64)

    scales = {
        name: float(np.mean(column[column > 0.0])) if (column > 0.0).any() else 1.0
        for name, column in zip(SIGNALS, values.T, strict=True)
    }

    rng = np.random.default_rng(seed)
    hits = np.flatnonzero(target > 0.0)
    misses = np.flatnonzero(target == 0.0)
    if not hits.size:
        raise RuntimeError("no stranding fell on a scored segment-day: nothing to fit")
    keep = min(len(misses), NEGATIVES_PER_POSITIVE * len(hits))
    sampled = rng.choice(misses, size=keep, replace=False)
    rows = np.concatenate([hits, sampled])

    columns = [
        transform(values[rows, i], scales[name]) for i, name in enumerate(SIGNALS)
    ]
    columns.append(logit(seasonal[rows]) - past.baseline)
    beta = irls(np.column_stack(columns), target[rows])

    intercept = float(beta[0]) - float(np.log(keep / max(len(misses), 1)))
    model = Model(
        intercept=intercept,
        weights=dict(
            zip(SIGNALS, (float(b) for b in beta[1 : 1 + len(SIGNALS)]), strict=True)
        ),
        scales=scales,
        seasonal_weight=float(beta[-1]),
        seasonal_baseline=past.baseline,
        fitted=True,
    )

    model.save()
    logger.info(
        "fit: %d positive(s) and %d sampled negative(s) of %d segment-day(s)",
        len(hits),
        keep,
        len(stored),
    )
    for name in SIGNALS:
        logger.info(
            "  %-14s weight %+.4f  scale %.5g", name, model.weights[name], scales[name]
        )
    logger.info("  %-14s weight %+.4f", "seasonal", model.seasonal_weight)
    logger.info("  %-14s %+.4f", "intercept", model.intercept)

    # Between-day spread is what makes a calm day look different from a storm.
    # If the model's variation is mostly spatial, no colour scale can create it.
    columns = [transform(values[:, i], scales[name]) for i, name in enumerate(SIGNALS)]
    columns.append(logit(seasonal) - past.baseline)
    everywhere = predict(np.column_stack(columns), model)
    by_day: dict[date, list[float]] = {}
    for row, chance in zip(stored, everywhere, strict=True):
        by_day.setdefault(row[0], []).append(float(chance))
    means = np.array([np.mean(values) for values in by_day.values()])
    spreads = np.array([np.std(values) for values in by_day.values()])
    logger.info(
        "  spread: between-day %.4g, within-day %.4g (ratio %.2f) -- "
        "a low ratio means the map will look much the same every day",
        float(np.std(means)),
        float(np.mean(spreads)),
        float(np.std(means) / np.mean(spreads)) if np.mean(spreads) > 0 else 0.0,
    )
    return model


def skill(session: Session, days: Sequence[date] | None = None) -> dict[str, float]:
    index = load_segments(session)
    past = history(session, index)
    positions = {segment_id: i for i, segment_id in enumerate(index.ids)}
    struck = set(past.observed.pairs)
    statement = select(
        SegmentRisk.day, SegmentRisk.coastal_segment_id, SegmentRisk.probability
    )
    if days:
        statement = statement.where(SegmentRisk.day.in_(days))
    rows = session.execute(statement).all()
    if not rows:
        return {"rows": 0.0, "positives": 0.0, "auc": 0.0}
    target = np.array(
        [1.0 if (positions.get(row[1], -1), row[0]) in struck else 0.0 for row in rows]
    )
    chance = np.array([float(row[2]) for row in rows])
    hits, misses = target.sum(), (1.0 - target).sum()
    if not hits or not misses:
        return {"rows": float(len(rows)), "positives": float(hits), "auc": 0.0}
    order = np.argsort(chance, kind="stable")
    ranks = np.empty(len(chance), dtype=np.float64)
    ranks[order] = np.arange(1, len(chance) + 1)
    auc = (ranks[target > 0].sum() - hits * (hits + 1) / 2) / (hits * misses)
    return {"rows": float(len(rows)), "positives": float(hits), "auc": float(auc)}


MAX_LOOKBACK_DAYS = MAX_DRIFT_DAYS + DRIFT_WINDOW_DAYS
