import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import copernicusmarine
import numpy as np
import xarray as xr
from sqlalchemy.orm import Session

from strandarr.analysis import Float, Int
from strandarr.analysis.geo import GRID, load_cells
from strandarr.config import settings
from strandarr.db import upsert
from strandarr.errors import NotReady
from strandarr.models import Sea, Wind
from strandarr.timeframe import CHUNK_EPOCH, DayRange, midnight

logger = logging.getLogger(__name__)
logging.getLogger("copernicusmarine").setLevel(logging.WARNING)

DAYS_PER_CHUNK = 10
COVERAGE_TTL_SECONDS = 3600
MARGIN_DEG = 0.2


@dataclass(frozen=True)
class Product:
    name: str
    datasets: tuple[str, ...]
    variables: tuple[str, ...]


CURRENTS = Product(
    "currents",
    (
        "cmems_mod_ibi_phy-cur_my_0.027deg_PT1H-m",
        "cmems_mod_ibi_phy_anfc_0.027deg-2D_PT1H-m",
    ),
    ("uo", "vo"),
)
WAVES = Product(
    "waves",
    (
        "cmems_mod_ibi_wav_my_0.027deg_PT1H-i",
        "cmems_mod_ibi_wav_anfc_0.027deg_PT1H-i",
    ),
    (
        "VSDX",
        "VSDY",
        "VHM0",
        "VMDR",
        "VTM02",
        "VHM0_SW1",
        "VMDR_SW1",
        "VTM01_SW1",
    ),
)
WIND = Product(
    "wind",
    (
        "cmems_obs-wind_glo_phy_my_l4_0.125deg_PT1H",
        "cmems_obs-wind_glo_phy_nrt_l4_0.125deg_PT1H",
    ),
    ("eastward_wind", "northward_wind"),
)
PRODUCTS = (CURRENTS, WAVES, WIND)

_coverage: dict[str, tuple[float, datetime, datetime]] = {}


def _credentials() -> dict[str, str]:
    if not settings.cmems_username or not settings.cmems_password:
        raise RuntimeError("CMEMS_USERNAME and CMEMS_PASSWORD are not set")
    return {"username": settings.cmems_username, "password": settings.cmems_password}


def _instant(value: np.datetime64) -> datetime:
    seconds = value.astype("datetime64[s]").astype(np.int64)
    return datetime.fromtimestamp(int(seconds), UTC)


def coverage(dataset: str) -> tuple[datetime, datetime]:
    cached = _coverage.get(dataset)
    if cached is not None and time.monotonic() - cached[0] < COVERAGE_TTL_SECONDS:
        return cached[1], cached[2]
    lat, lon = GRID.bbox.min_lat + 1, GRID.bbox.min_lon + 1
    times = copernicusmarine.open_dataset(
        dataset_id=dataset,
        minimum_latitude=lat,
        maximum_latitude=lat,
        minimum_longitude=lon,
        maximum_longitude=lon,
        **_credentials(),
    ).time.values
    first, last = _instant(times[0]), _instant(times[-1])
    _coverage[dataset] = (time.monotonic(), first, last)
    return first, last


def choose(product: Product, start: datetime, end: datetime) -> str | None:
    usable = []
    for dataset in product.datasets:
        first, last = coverage(dataset)
        if first > start or last < start:
            continue
        if last >= end - timedelta(hours=1):
            return dataset
        usable.append((last, dataset))
    return max(usable)[1] if usable else None


def path(product: Product, chunk: date) -> Path:
    return Path(settings.forcing_dir) / f"{product.name}-{chunk:%Y%m%d}.nc"


def chunk_of(day: date) -> date:
    return CHUNK_EPOCH + timedelta(
        days=(day - CHUNK_EPOCH).days // DAYS_PER_CHUNK * DAYS_PER_CHUNK
    )


def download(product: Product, chunk: date, start: datetime, end: datetime) -> Path:
    dataset = choose(product, start, end)
    if dataset is None:
        raise NotReady(f"cmems {product.name}: no dataset covers {start:%Y-%m-%d}")
    target = path(product, chunk)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f"{target.stem}.part.nc")
    min_lon, min_lat, max_lon, max_lat = GRID.bbox.corners()
    started = time.monotonic()
    copernicusmarine.subset(
        dataset_id=dataset,
        variables=list(product.variables),
        minimum_longitude=min_lon - MARGIN_DEG,
        maximum_longitude=max_lon + MARGIN_DEG,
        minimum_latitude=min_lat - MARGIN_DEG,
        maximum_latitude=max_lat + MARGIN_DEG,
        start_datetime=start.strftime("%Y-%m-%dT%H:%M:%S"),
        end_datetime=(end - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S"),
        output_directory=str(partial.parent),
        output_filename=partial.name,
        overwrite=True,
        disable_progress_bar=True,
        **_credentials(),
    )
    partial.replace(target)
    for leftover in partial.parent.glob(f"{partial.name}.*"):
        leftover.unlink()
    logger.info(
        "cmems %s: %s from %s in %.0fs",
        product.name,
        chunk,
        dataset,
        time.monotonic() - started,
    )
    return target


def _grid_index(values: object, origin: float) -> Int:
    degrees = np.asarray(values, dtype=np.float64)
    return np.asarray(np.rint((degrees - origin) / GRID.step_deg), dtype=np.int32)


class Binning:
    def __init__(self, dataset: xr.Dataset, cells: list[tuple[int, float, float]]):
        min_lat, min_lon = GRID.bbox.min_lat, GRID.bbox.min_lon
        cell_rows = _grid_index([lat for _, lat, _ in cells], min_lat)
        cell_columns = _grid_index([lon for _, _, lon in cells], min_lon)
        lookup = np.full(
            (cell_rows.max() + 1, cell_columns.max() + 1), -1, dtype=np.int32
        )
        lookup[cell_rows, cell_columns] = np.arange(len(cells), dtype=np.int32)
        rows = _grid_index(dataset.latitude.values, min_lat)
        columns = _grid_index(dataset.longitude.values, min_lon)
        row_ok = (rows >= 0) & (rows < lookup.shape[0])
        column_ok = (columns >= 0) & (columns < lookup.shape[1])
        target = np.full((len(rows), len(columns)), -1, dtype=np.int32)
        target[np.ix_(row_ok, column_ok)] = lookup[
            np.ix_(rows[row_ok], columns[column_ok])
        ]
        self.target: Int = target.ravel()
        self.cells = len(cells)
        self.times = [_instant(value) for value in dataset.time.values]

    def mean(self, values: Float) -> Float:
        hours = values.shape[0]
        flat = values.reshape(hours, -1)
        valid = np.isfinite(flat) & (self.target >= 0)[None, :]
        hour, spot = np.nonzero(valid)
        slot = hour * self.cells + self.target[spot]
        size = hours * self.cells
        total = np.bincount(slot, weights=flat[hour, spot], minlength=size)
        count = np.bincount(slot, minlength=size)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.asarray((total / count).reshape(hours, self.cells))


def _field(dataset: xr.Dataset, name: str) -> Float:
    return np.asarray(dataset[name].squeeze(drop=True).values, dtype=np.float64)


def _heading(binning: Binning, degrees: Float) -> Float:
    radians = np.radians(degrees)
    east = binning.mean(np.sin(radians))
    north = binning.mean(np.cos(radians))
    return np.asarray(np.degrees(np.arctan2(east, north)) % 360.0)


def _value(values: Float, hour: int, cell: int) -> float | None:
    value = float(values[hour, cell])
    return value if np.isfinite(value) else None


def _hours(binning: Binning, columns: dict[str, Float]) -> Iterator[tuple[int, int]]:
    present = np.zeros((len(binning.times), binning.cells), dtype=bool)
    for values in columns.values():
        present |= np.isfinite(values)
    for hour, cell in zip(*np.nonzero(present), strict=True):
        yield int(hour), int(cell)


def sea_rows(
    currents: xr.Dataset, waves: xr.Dataset, cells: list[tuple[int, float, float]]
) -> list[Sea]:
    now = datetime.now(UTC)
    rows: dict[tuple[datetime, int], Sea] = {}
    for binning, values in (_currents(currents, cells), _waves(waves, cells)):
        for hour, cell in _hours(binning, values):
            at = binning.times[hour]
            row = rows.setdefault(
                (at, cell),
                Sea(valid_at=at, cell_id=cells[cell][0], forecast=at > now),
            )
            for name, column in values.items():
                setattr(row, name, _value(column, hour, cell))
    return list(rows.values())


def _currents(
    dataset: xr.Dataset, cells: list[tuple[int, float, float]]
) -> tuple[Binning, dict[str, Float]]:
    binning = Binning(dataset, cells)
    east = binning.mean(_field(dataset, "uo"))
    north = binning.mean(_field(dataset, "vo"))
    return binning, {
        "current_speed_kmh": np.hypot(east, north) * 3.6,
        "current_direction_deg": np.degrees(np.arctan2(east, north)) % 360.0,
    }


def _waves(
    dataset: xr.Dataset, cells: list[tuple[int, float, float]]
) -> tuple[Binning, dict[str, Float]]:
    binning = Binning(dataset, cells)
    return binning, {
        "wave_height_m": binning.mean(_field(dataset, "VHM0")),
        "wave_direction_deg": _heading(binning, _field(dataset, "VMDR")),
        "wave_period_s": binning.mean(_field(dataset, "VTM02")),
        "swell_height_m": binning.mean(_field(dataset, "VHM0_SW1")),
        "swell_direction_deg": _heading(binning, _field(dataset, "VMDR_SW1")),
        "swell_period_s": binning.mean(_field(dataset, "VTM01_SW1")),
    }


def wind_rows(dataset: xr.Dataset, cells: list[tuple[int, float, float]]) -> list[Wind]:
    binning = Binning(dataset, cells)
    east = binning.mean(_field(dataset, "eastward_wind"))
    north = binning.mean(_field(dataset, "northward_wind"))
    speed = np.hypot(east, north) * 3.6
    blowing_from = (np.degrees(np.arctan2(east, north)) + 180.0) % 360.0
    return [
        Wind(
            valid_at=binning.times[hour],
            cell_id=cells[cell][0],
            forecast=False,
            speed_kmh=float(speed[hour, cell]),
            direction_deg=float(blowing_from[hour, cell]),
        )
        for hour, cell in zip(*np.nonzero(np.isfinite(speed)), strict=True)
    ]


def archive(session: Session, days: DayRange) -> int:
    chunk = chunk_of(days.start)
    start, end = days.bounds()
    cells = load_cells(session)
    currents_file = download(CURRENTS, chunk, start, end)
    waves_file = download(WAVES, chunk, start, end)
    with (
        xr.open_dataset(currents_file) as currents,
        xr.open_dataset(waves_file) as waves,
    ):
        sea = sea_rows(currents, waves, cells)
    winds: list[Wind] = []
    if choose(WIND, start, end) is not None:
        wind_file = download(WIND, chunk, start, end)
        with xr.open_dataset(wind_file) as wind:
            winds = wind_rows(wind, cells)
        wind_file.unlink()
    written = upsert(session, Sea, sea, overwrite=True)
    written += upsert(session, Wind, winds, overwrite=True)
    session.commit()
    logger.info(
        "cmems: %s..%s -> %d sea and %d wind row(s)",
        days.start,
        days.end,
        len(sea),
        len(winds),
    )
    return written


def forcing(start: datetime, end: datetime) -> tuple[list[Path], list[Path], datetime]:
    first = chunk_of(start.date())
    chunks = []
    chunk = first
    while midnight(chunk) < end:
        chunks.append(chunk)
        chunk += timedelta(days=DAYS_PER_CHUNK)
    currents: list[Path] = []
    waves: list[Path] = []
    reached = start
    for chunk in chunks:
        current, wave = path(CURRENTS, chunk), path(WAVES, chunk)
        if not current.exists() or not wave.exists():
            break
        with xr.open_dataset(current) as flow, xr.open_dataset(wave) as sea:
            last = min(_instant(flow.time.values[-1]), _instant(sea.time.values[-1]))
        currents.append(current)
        waves.append(wave)
        reached = last + timedelta(hours=1)
        if reached < midnight(chunk + timedelta(days=DAYS_PER_CHUNK)):
            break
    return currents, waves, min(reached, end)


def prune(keep_from: date) -> int:
    removed = 0
    for product in PRODUCTS:
        for stale in Path(settings.forcing_dir).glob(f"{product.name}-*.nc"):
            chunk = datetime.strptime(stale.name.split("-")[1][:8], "%Y%m%d").date()
            if chunk + timedelta(days=DAYS_PER_CHUNK) <= keep_from:
                stale.unlink()
                removed += 1
    if removed:
        logger.info("cmems: pruned %d forcing file(s) before %s", removed, keep_from)
    return removed
