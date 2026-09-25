import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import xarray as xr
from sqlalchemy import select
from sqlalchemy.orm import Session

from strandarr.analysis import Float
from strandarr.analysis.geo import GRID, Coast, coast, load_segments
from strandarr.db import replace
from strandarr.errors import NotReady
from strandarr.models import Cell, DriftDaily, Vessel, VesselPosition, Wind
from strandarr.sources import cmems
from strandarr.timeframe import DayRange, midnight

logger = logging.getLogger(__name__)

WIND_DRIFT_FACTOR = 0.012
WIND_DRIFT_SPREAD = 0.004
HORIZONTAL_DIFFUSIVITY_M2S = 25.0
BUOYANCY_RATE = 0.31
FLOAT_HALF_LIFE_DAYS = 12.0
MAX_DRIFT_DAYS = 20
SNAP_KM = 15.0
TIME_STEP_SECONDS = 3600

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

PARTICLES_PER_SEED = 16
MAX_PARTICLES = 400_000
MIN_PARTICLES_PER_SEED = 4
WEIGHT_FLOOR = 1e-9
FILL_PASSES = 6


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


def seeds(positions: Sequence[Any], day: date) -> list[Seed]:
    start = midnight(day)
    grouped: dict[tuple[int, float, float], float] = {}
    for position in positions:
        effort = position.effort_hours
        if not effort or effort <= 0:
            continue
        hour = int((position.recorded_at - start).total_seconds() // 3600)
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


def _fill(field: Float) -> Float:
    filled = field.copy()
    for _ in range(FILL_PASSES):
        holes = np.isnan(filled)
        if not holes.any():
            break
        padded = np.pad(filled, ((0, 0), (1, 1), (1, 1)), constant_values=np.nan)
        sides = np.stack(
            (
                padded[:, :-2, 1:-1],
                padded[:, 2:, 1:-1],
                padded[:, 1:-1, :-2],
                padded[:, 1:-1, 2:],
            )
        )
        counts = np.isfinite(sides).sum(axis=0)
        means = np.nansum(sides, axis=0) / np.maximum(counts, 1)
        usable = holes & (counts > 0)
        filled[usable] = means[usable]
    return np.nan_to_num(filled)


def wind_field(session: Session, start: datetime, end: datetime) -> xr.Dataset:
    lat_axis, lon_axis = GRID.axes()
    lats = np.asarray(lat_axis, dtype=np.float64)
    lons = np.asarray(lon_axis, dtype=np.float64)
    hours = int((end - start).total_seconds() // 3600)
    east = np.full((hours, len(lats), len(lons)), np.nan)
    north = np.full_like(east, np.nan)
    rows = session.execute(
        select(Wind.valid_at, Cell.lat, Cell.lon, Wind.speed_kmh, Wind.direction_deg)
        .join(Cell, Cell.id == Wind.cell_id)
        .where(Wind.valid_at >= start, Wind.valid_at < end)
    ).all()
    if rows:
        hour = np.array([int((row[0] - start).total_seconds() // 3600) for row in rows])
        i = np.rint((np.array([row[1] for row in rows]) - lats[0]) / GRID.step_deg)
        j = np.rint((np.array([row[2] for row in rows]) - lons[0]) / GRID.step_deg)
        speed = np.array([row[3] for row in rows], dtype=np.float64) / 3.6
        blowing = np.radians(np.array([row[4] for row in rows], dtype=np.float64) + 180)
        east[hour, i.astype(int), j.astype(int)] = speed * np.sin(blowing)
        north[hour, i.astype(int), j.astype(int)] = speed * np.cos(blowing)
    times = np.array(
        [
            np.datetime64(start.replace(tzinfo=None) + timedelta(hours=h))
            for h in range(hours)
        ],
        dtype="datetime64[ns]",
    )
    return xr.Dataset(
        {
            "x_wind": (
                ("time", "lat", "lon"),
                _fill(east),
                {"standard_name": "x_wind"},
            ),
            "y_wind": (
                ("time", "lat", "lon"),
                _fill(north),
                {"standard_name": "y_wind"},
            ),
        },
        coords={
            "time": ("time", times),
            "lat": ("lat", lats, {"standard_name": "latitude"}),
            "lon": ("lon", lons, {"standard_name": "longitude"}),
        },
    )


@dataclass(frozen=True)
class Result:
    landed: dict[tuple[date, int], float]
    particles: int
    released: float
    stranded: float
    hours: int


def simulate(
    seed_list: Sequence[Seed],
    readers: list[Any],
    start: datetime,
    end: datetime,
    shore: Coast,
) -> Result:
    hours = int((end - start).total_seconds() // 3600)
    if not seed_list:
        return Result({}, 0, 0.0, 0.0, hours)
    rng = np.random.default_rng(0)
    per_seed = max(
        MIN_PARTICLES_PER_SEED, min(PARTICLES_PER_SEED, MAX_PARTICLES // len(seed_list))
    )
    count = len(seed_list) * per_seed
    jitter = GRID.step_deg / 2
    birth = np.repeat([seed.hour for seed in seed_list], per_seed)
    lat = np.repeat([seed.lat for seed in seed_list], per_seed)
    lon = np.repeat([seed.lon for seed in seed_list], per_seed)
    weight = np.repeat([seed.weight for seed in seed_list], per_seed) / per_seed
    windage = np.clip(rng.normal(WIND_DRIFT_FACTOR, WIND_DRIFT_SPREAD, count), 0, None)
    naive = start.replace(tzinfo=None)

    from opendrift.models.oceandrift import OceanDrift

    model = OceanDrift(loglevel=50)
    model.add_reader(readers)
    model.set_config("general:coastline_action", "stranding")
    model.set_config("drift:advection_scheme", "runge-kutta")
    model.set_config("drift:stokes_drift", True)
    model.set_config(
        "environment:constant:horizontal_diffusivity", HORIZONTAL_DIFFUSIVITY_M2S
    )
    model.set_config("environment:fallback:x_sea_water_velocity", None)
    model.set_config("environment:fallback:y_sea_water_velocity", None)
    model.seed_elements(
        lon=lon + rng.uniform(-jitter, jitter, count),
        lat=lat + rng.uniform(-jitter, jitter, count),
        time=[naive + timedelta(hours=int(h)) for h in birth],
        wind_drift_factor=windage,
        z=0,
    )
    model.run(
        end_time=end.replace(tzinfo=None),
        time_step=TIME_STEP_SECONDS,
        time_step_output=hours * 3600,
    )

    gone = model.elements_deactivated
    stranded = gone.status == model.status_categories.index("stranded")
    index = gone.ID[stranded].astype(np.int64) - 1
    age_hours = gone.age_seconds[stranded] / 3600.0
    landed_weight = weight[index] * 0.5 ** (age_hours / (FLOAT_HALF_LIFE_DAYS * 24))
    landed_hour = birth[index] + age_hours
    segment, distance = shore.lookup(
        np.asarray(gone.lat[stranded], dtype=np.float64),
        np.asarray(gone.lon[stranded], dtype=np.float64),
    )
    near = (segment >= 0) & (distance <= SNAP_KM)
    landed: dict[tuple[date, int], float] = {}
    for position, hour, value in zip(
        segment[near], landed_hour[near], landed_weight[near], strict=True
    ):
        key = ((start + timedelta(hours=float(hour))).date(), int(position))
        landed[key] = landed.get(key, 0.0) + float(value)
    return Result(
        landed=landed,
        particles=count,
        released=float(weight.sum()),
        stranded=float(landed_weight[near].sum()),
        hours=hours,
    )


def run(session: Session, day: date) -> int:
    start, stop = DayRange.of(day).bounds()
    positions = session.execute(
        select(
            VesselPosition.recorded_at,
            VesselPosition.lat,
            VesselPosition.lon,
            VesselPosition.effort_hours,
            Vessel.gear_type,
        )
        .join(Vessel, Vessel.id == VesselPosition.vessel_id)
        .where(VesselPosition.recorded_at >= start, VesselPosition.recorded_at < stop)
    ).all()
    if not positions:
        raise NotReady(f"no vessel positions stored for {day}")

    horizon = start + timedelta(days=MAX_DRIFT_DAYS)
    files, end = cmems.forcing(start, horizon)
    needed = min(horizon, midnight(date.today()))
    if end < max(needed, stop):
        raise NotReady(
            f"CMEMS forcing for {day} reaches {end:%Y-%m-%d %H:%M}, "
            f"needs {max(needed, stop):%Y-%m-%d %H:%M}"
        )

    from opendrift.readers import reader_netCDF_CF_generic

    index = load_segments(session)
    started = time.monotonic()
    readers = [reader_netCDF_CF_generic.Reader(str(file)) for file in files]
    readers.append(reader_netCDF_CF_generic.Reader(wind_field(session, start, end)))
    result = simulate(seeds(positions, day), readers, start, end, coast(index, SNAP_KM))

    replace(
        session,
        DriftDaily,
        [
            DriftDaily(
                release_day=day,
                day=landed,
                coastal_segment_id=index.ids[position],
                drift_index=value,
            )
            for (landed, position), value in result.landed.items()
        ],
        DriftDaily.release_day == day,
    )
    session.commit()
    logger.info(
        "drift %s: %d particle(s), %.3f released -> %.3f stranded on %d segment-day(s), "
        "%dh of forcing in %.0fs%s",
        day,
        result.particles,
        result.released,
        result.stranded,
        len(result.landed),
        result.hours,
        time.monotonic() - started,
        "" if result.hours >= MAX_DRIFT_DAYS * 24 else ", provisional",
    )
    return len(result.landed)
