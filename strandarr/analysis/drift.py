import logging
import math
import warnings
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
from numpy.typing import NDArray

from strandarr import grid
from strandarr.config import settings
from strandarr.models import CoastalSegment, VesselPosition

logger = logging.getLogger(__name__)

MODEL_VERSION = "v1"

WIND_DRIFT_FACTOR = 0.012
WIND_DRIFT_SPREAD = 0.004
HORIZONTAL_DIFFUSIVITY_M2S = 25.0
BUOYANCY_RATE = 0.31
FLOAT_HALF_LIFE_DAYS = 12.0
MAX_DRIFT_DAYS = 20
BEACHING_DISTANCE_KM = 6.0
BEACHING_RATE_PER_HOUR = 0.06
TIDE_GAIN = 0.6
WAVE_GAIN = 0.8
WAVE_REFERENCE_M = 3.0

GEAR_WEIGHTS: dict[str, float] = {
    "trawlers": 1.0,
    "set_gillnets": 1.0,
    "fixed_gear": 0.6,
    "seiners": 0.2,
    "purse_seines": 0.2,
    "tuna_purse_seines": 0.1,
    "other_purse_seines": 0.2,
    "other_seines": 0.2,
    "trollers": 0.1,
    "set_longlines": 0.05,
    "drifting_longlines": 0.05,
    "pots_and_traps": 0.05,
    "squid_jigger": 0.05,
    "dredge_fishing": 0.05,
}
DEFAULT_GEAR_WEIGHT = 0.3

SEASON_WEIGHTS: tuple[float, ...] = (
    1.00,
    1.00,
    0.90,
    0.50,
    0.30,
    0.20,
    0.20,
    0.20,
    0.25,
    0.40,
    0.70,
    0.95,
)

BYCATCH_PER_EFFORT_HOUR = 1.0
DETECTION_RATE = 1.0

PARTICLES_PER_SEED = 32
MAX_PARTICLES = 400_000
MIN_PARTICLES_PER_SEED = 6
WEIGHT_FLOOR = 1e-9

COAST_RASTER_DEG = 0.02
METRES_PER_DEGREE = 111_320.0
SECONDS_PER_STEP = 3600.0

DRIFT_HOURS = MAX_DRIFT_DAYS * 24

MEASUREMENTS = (
    "current_speed_kmh",
    "current_direction_deg",
    "wind_speed_kmh",
    "wind_direction_deg",
    "sea_level_m",
    "wave_height_m",
)
READING_WIDTH = 4 + len(MEASUREMENTS)

Float = NDArray[np.float64]
Int = NDArray[np.int32]


@dataclass(frozen=True)
class Forcing:
    hours: int
    lats: Float
    lons: Float
    fields: Float

    def sample(self, step: int, lat: Float, lon: Float) -> Float:
        span = settings.grid_step_deg
        row = (lat - self.lats[0]) / span
        column = (lon - self.lons[0]) / span
        row0 = np.clip(np.floor(row), 0, len(self.lats) - 2).astype(np.int32)
        col0 = np.clip(np.floor(column), 0, len(self.lons) - 2).astype(np.int32)
        fr = np.clip(row - row0, 0.0, 1.0)
        fc = np.clip(column - col0, 0.0, 1.0)

        plane = self.fields[:, step]
        top = plane[:, row0, col0] * (1 - fc) + plane[:, row0, col0 + 1] * fc
        bottom = plane[:, row0 + 1, col0] * (1 - fc) + plane[:, row0 + 1, col0 + 1] * fc
        return np.asarray(top * (1 - fr) + bottom * fr, dtype=np.float64)


def build_forcing(batches: Iterable[Sequence[Sequence[Any]]]) -> Forcing:
    lat_axis, lon_axis = grid.axes()
    lats = np.asarray(lat_axis, dtype=np.float64)
    lons = np.asarray(lon_axis, dtype=np.float64)
    raw = np.full((len(MEASUREMENTS), DRIFT_HOURS, len(lats), len(lons)), np.nan)

    columns: list[list[Any]] = [[] for _ in range(READING_WIDTH)]
    for batch in batches:
        for column, incoming in zip(columns, zip(*batch, strict=True), strict=True):
            column.extend(incoming)

    covered: set[int] = set()
    if columns[0]:
        span = settings.grid_step_deg
        hour = np.asarray(columns[0], dtype=np.int64)
        rank = np.asarray(columns[1], dtype=np.int64)
        i = np.rint((np.asarray(columns[2], dtype=np.float64) - lats[0]) / span)
        j = np.rint((np.asarray(columns[3], dtype=np.float64) - lons[0]) / span)
        inside = (
            (hour >= 0)
            & (hour < DRIFT_HOURS)
            & (i >= 0)
            & (i < len(lats))
            & (j >= 0)
            & (j < len(lons))
        )
        hour, rank = hour[inside], rank[inside]
        i = i[inside].astype(np.int64)
        j = j[inside].astype(np.int64)
        measured = [
            np.asarray(columns[4 + offset], dtype=np.float64)[inside]
            for offset in range(len(MEASUREMENTS))
        ]
        for level in np.unique(rank):
            at = rank == level
            rows, cols, slots = hour[at], i[at], j[at]
            for offset, values in enumerate(measured):
                value = values[at]
                take = ~np.isnan(value) & np.isnan(raw[offset, rows, cols, slots])
                raw[offset][rows[take], cols[take], slots[take]] = value[take]
        covered = set(hour.tolist())

    speed, course, wind_speed, wind_course, sea_level, wave = raw
    current = np.nan_to_num(speed, nan=0.0) / 3.6
    heading = np.radians(np.nan_to_num(course, nan=0.0))
    wind = np.nan_to_num(wind_speed, nan=0.0) / 3.6
    blowing = np.radians(np.nan_to_num(wind_course, nan=0.0) + 180.0)

    return Forcing(
        hours=_contiguous_hours(covered),
        lats=lats,
        lons=lons,
        fields=np.asarray(
            [
                _fill_gaps(current * np.sin(heading)),
                _fill_gaps(current * np.cos(heading)),
                _fill_gaps(wind * np.sin(blowing)),
                _fill_gaps(wind * np.cos(blowing)),
                _standardise(sea_level),
                _fill_gaps(np.nan_to_num(wave, nan=0.0)),
            ]
        ),
    )


def _contiguous_hours(covered: set[int]) -> int:
    hour = 0
    while hour < DRIFT_HOURS and hour in covered:
        hour += 1
    return hour


def _fill_gaps(field: Float, passes: int = 6) -> Float:
    filled = field.copy()
    for _ in range(passes):
        holes = filled == 0.0
        if not holes.any():
            break
        padded = np.pad(filled, ((0, 0), (1, 1), (1, 1)), mode="edge")
        sides = np.stack(
            (
                padded[:, :-2, 1:-1],
                padded[:, 2:, 1:-1],
                padded[:, 1:-1, :-2],
                padded[:, 1:-1, 2:],
            )
        )
        neighbours = sides.sum(axis=0)
        counts = (sides != 0.0).sum(axis=0)
        usable = holes & (counts > 0)
        filled[usable] = neighbours[usable] / counts[usable]
    return filled


def _standardise(sea_level: Float) -> Float:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mean = np.nanmean(sea_level, axis=0, keepdims=True)
        spread = np.nanstd(sea_level, axis=0, keepdims=True)
    z = (sea_level - mean) / np.where(spread > 1e-6, spread, 1.0)
    return np.clip(np.nan_to_num(z, nan=0.0), -2.0, 2.0)


@dataclass(frozen=True)
class Coast:
    segment_ids: list[int]
    nearest: Int
    distance_km: Float

    def lookup(self, lat: Float, lon: Float) -> tuple[Int, Float]:
        min_lon, min_lat, _, _ = grid.BBOX
        rows, columns = self.nearest.shape
        i = np.clip(((lat - min_lat) / COAST_RASTER_DEG).astype(np.int32), 0, rows - 1)
        j = np.clip(
            ((lon - min_lon) / COAST_RASTER_DEG).astype(np.int32), 0, columns - 1
        )
        return self.nearest[i, j], self.distance_km[i, j]


_coast: tuple[tuple[int, ...], Coast] | None = None


def build_coast(segments: Sequence[CoastalSegment]) -> Coast:
    global _coast
    key = tuple(segment.id for segment in segments)
    if _coast is not None and _coast[0] == key:
        return _coast[1]

    min_lon, min_lat, max_lon, max_lat = grid.BBOX
    rows = int((max_lat - min_lat) / COAST_RASTER_DEG) + 1
    columns = int((max_lon - min_lon) / COAST_RASTER_DEG) + 1
    nearest = np.full((rows, columns), -1, dtype=np.int32)
    distance = np.full((rows, columns), np.inf, dtype=np.float64)
    lat_axis = min_lat + np.arange(rows, dtype=np.float64) * COAST_RASTER_DEG
    lon_axis = min_lon + np.arange(columns, dtype=np.float64) * COAST_RASTER_DEG

    for index, segment in enumerate(segments):
        for lon, lat in segment.path or [[segment.center_lon, segment.center_lat]]:
            scale = 111.3 * math.cos(math.radians(lat))
            half_lat = BEACHING_DISTANCE_KM / 110.6
            half_lon = BEACHING_DISTANCE_KM / max(1.0, scale)
            i0 = max(0, int((lat - half_lat - min_lat) / COAST_RASTER_DEG))
            i1 = min(rows, int((lat + half_lat - min_lat) / COAST_RASTER_DEG) + 2)
            j0 = max(0, int((lon - half_lon - min_lon) / COAST_RASTER_DEG))
            j1 = min(columns, int((lon + half_lon - min_lon) / COAST_RASTER_DEG) + 2)
            if i0 >= i1 or j0 >= j1:
                continue
            local = np.hypot(
                (lat_axis[i0:i1] - lat)[:, None] * 110.6,
                (lon_axis[j0:j1] - lon)[None, :] * scale,
            )
            window = distance[i0:i1, j0:j1]
            closer = local < window
            window[closer] = local[closer]
            nearest[i0:i1, j0:j1][closer] = index

    coast = Coast(
        segment_ids=[segment.id for segment in segments],
        nearest=nearest,
        distance_km=distance,
    )
    _coast = (key, coast)
    logger.info(
        "coast raster: %d segment(s), %dx%d cells", len(segments), rows, columns
    )
    return coast


@dataclass(frozen=True)
class Seed:
    hour: int
    lat: float
    lon: float
    weight: float


def gear_weight(gear_type: str | None) -> float:
    if not gear_type:
        return DEFAULT_GEAR_WEIGHT
    return GEAR_WEIGHTS.get(gear_type.strip().lower(), DEFAULT_GEAR_WEIGHT)


def seeds(positions: Sequence[VesselPosition], day: date) -> list[Seed]:
    midnight = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    grouped: dict[tuple[int, float, float], float] = {}
    for position in positions:
        effort = position.effort_hours
        if not effort or effort <= 0:
            continue
        hour = int((position.recorded_at - midnight).total_seconds() // 3600)
        if not 0 <= hour < 24:
            continue
        key = (hour, round(position.lat, 3), round(position.lon, 3))
        grouped[key] = grouped.get(key, 0.0) + effort * gear_weight(position.gear_type)

    scale = (
        SEASON_WEIGHTS[day.month - 1]
        * BUOYANCY_RATE
        * BYCATCH_PER_EFFORT_HOUR
        * DETECTION_RATE
    )
    return [
        Seed(hour=hour, lat=lat, lon=lon, weight=weight * scale)
        for (hour, lat, lon), weight in grouped.items()
        if weight * scale > WEIGHT_FLOOR
    ]


@dataclass(frozen=True)
class Arrival:
    segment_id: int
    hour: int
    expected_count: float


@dataclass(frozen=True)
class Result:
    arrivals: list[Arrival]
    particles: int
    released_weight: float
    stranded_weight: float
    forcing_hours: int

    @property
    def complete(self) -> bool:
        return self.forcing_hours >= DRIFT_HOURS


def simulate(
    seed_list: Sequence[Seed],
    forcing: Forcing,
    coast: Coast,
    rng: np.random.Generator | None = None,
) -> Result:
    generator = rng if rng is not None else np.random.default_rng(0)
    steps = min(DRIFT_HOURS, forcing.hours)
    if not seed_list or steps <= 0:
        return Result([], 0, 0.0, 0.0, forcing.hours)

    per_seed = max(
        MIN_PARTICLES_PER_SEED, min(PARTICLES_PER_SEED, MAX_PARTICLES // len(seed_list))
    )
    count = len(seed_list) * per_seed
    jitter = settings.grid_step_deg / 2

    birth = np.repeat([seed.hour for seed in seed_list], per_seed)
    lat = np.repeat([seed.lat for seed in seed_list], per_seed) + generator.uniform(
        -jitter, jitter, count
    )
    lon = np.repeat([seed.lon for seed in seed_list], per_seed) + generator.uniform(
        -jitter, jitter, count
    )
    weight = np.repeat([seed.weight for seed in seed_list], per_seed) / per_seed
    windage = np.clip(
        generator.normal(WIND_DRIFT_FACTOR, WIND_DRIFT_SPREAD, count), 0.0, None
    )

    released = float(weight.sum())
    alive = np.zeros(count, dtype=bool)
    moored = np.zeros(count, dtype=bool)
    moor_segment = np.full(count, -1, dtype=np.int32)
    deposits = np.zeros((len(coast.segment_ids), steps))

    min_lon, min_lat, max_lon, max_lat = grid.BBOX
    decay = 0.5 ** (1.0 / (FLOAT_HALF_LIFE_DAYS * 24.0))
    walk = math.sqrt(2 * HORIZONTAL_DIFFUSIVITY_M2S * SECONDS_PER_STEP)
    last_birth = int(birth.max())

    for step in range(steps):
        alive |= birth == step
        active = alive & (weight > WEIGHT_FLOOR)
        if not active.any():
            if step > last_birth:
                break
            continue

        index = np.flatnonzero(active)
        here_lat, here_lon = lat[index], lon[index]
        gust = windage[index]

        current_u, current_v, wind_u, wind_v, tide, wave = forcing.sample(
            step, here_lat, here_lon
        )
        east = current_u + gust * wind_u
        north = current_v + gust * wind_v

        scale = max(math.cos(math.radians(float(np.mean(here_lat)))), 0.1)
        half = SECONDS_PER_STEP / 2
        mid_u, mid_v, mid_wu, mid_wv, _, _ = forcing.sample(
            step,
            here_lat + north * half / METRES_PER_DEGREE,
            here_lon + east * half / (METRES_PER_DEGREE * scale),
        )
        east = 0.5 * (east + mid_u + gust * mid_wu)
        north = 0.5 * (north + mid_v + gust * mid_wv)

        cos_lat = np.maximum(np.cos(np.radians(here_lat)), 0.1)
        next_lat = (
            here_lat
            + (north * SECONDS_PER_STEP + generator.normal(0.0, walk, len(index)))
            / METRES_PER_DEGREE
        )
        next_lon = here_lon + (
            east * SECONDS_PER_STEP + generator.normal(0.0, walk, len(index))
        ) / (METRES_PER_DEGREE * cos_lat)

        segment, distance = coast.lookup(next_lat, next_lon)
        arrived = (segment >= 0) & (distance <= BEACHING_DISTANCE_KM)
        held = moored[index]
        leaving = held & ~arrived
        if leaving.any():
            next_lat = np.where(leaving, here_lat, next_lat)
            next_lon = np.where(leaving, here_lon, next_lon)
            segment = np.where(leaving, moor_segment[index], segment)
        near = arrived | held

        lat[index] = next_lat
        lon[index] = next_lon
        weight[index] *= decay

        landing = index[near]
        if landing.size:
            chance = (
                BEACHING_RATE_PER_HOUR
                * (1.0 + TIDE_GAIN * tide[near] / 2.0)
                * (1.0 + WAVE_GAIN * np.clip(wave[near] / WAVE_REFERENCE_M, 0.0, 1.0))
            )
            landed = weight[landing] * np.clip(chance, 0.0, 1.0)
            weight[landing] -= landed
            np.add.at(deposits[:, step], segment[near], landed)

            fresh = arrived & ~held
            moored[index[fresh]] = True
            moor_segment[index[fresh]] = segment[fresh]

        escaped = ~near & (
            (next_lat < min_lat)
            | (next_lat > max_lat)
            | (next_lon < min_lon)
            | (next_lon > max_lon)
        )
        if escaped.any():
            alive[index[escaped]] = False

    where, hours = np.nonzero(deposits > WEIGHT_FLOOR)
    return Result(
        arrivals=[
            Arrival(
                segment_id=coast.segment_ids[segment],
                hour=int(hour),
                expected_count=float(deposits[segment, hour]),
            )
            for segment, hour in zip(where, hours, strict=True)
        ],
        particles=count,
        released_weight=released,
        stranded_weight=float(deposits.sum()),
        forcing_hours=forcing.hours,
    )


def window(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    return start, start + timedelta(days=MAX_DRIFT_DAYS)
