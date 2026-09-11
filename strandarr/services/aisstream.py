import asyncio
import contextlib
import logging
import signal
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.orm import Session as OrmSession

from strandarr.connectors.aisstream import stream
from strandarr.models.vessel_position import VesselPosition
from strandarr.repositories import store
from strandarr.repositories.db import Session
from strandarr.services.schedule import BBOX, SOURCE_AIS_LIVE, SOURCE_FISHING
from strandarr.utils import log
from strandarr.utils.config import settings

logger = logging.getLogger(__name__)

WRITE_CHECK_SECONDS = 60
MAX_BUFFER = 5000
FLEET_REFRESH_SECONDS = 1800


def _hour(at: datetime) -> datetime:
    return at.replace(minute=0, second=0, microsecond=0)


def _last_known(
    session: OrmSession, column: InstrumentedAttribute[str | None]
) -> dict[str, str]:
    stmt = (
        select(VesselPosition.mmsi, column)
        .where(VesselPosition.source == SOURCE_FISHING, column.isnot(None))
        .distinct(VesselPosition.mmsi)
        .order_by(VesselPosition.mmsi, VesselPosition.recorded_at.desc())
    )
    return {
        mmsi: value for mmsi, value in session.execute(stmt).all() if value is not None
    }


def _fishing_fleet() -> dict[str, dict[str, str]]:
    with Session() as session:
        fleet: dict[str, dict[str, str]] = {
            mmsi: {}
            for mmsi in session.execute(
                select(VesselPosition.mmsi)
                .where(VesselPosition.source == SOURCE_FISHING)
                .distinct()
            ).scalars()
        }
        for field, column in (
            ("flag", VesselPosition.flag),
            ("gear_type", VesselPosition.gear_type),
            ("vessel_type", VesselPosition.vessel_type),
            ("name", VesselPosition.ship_name),
        ):
            for mmsi, value in _last_known(session, column).items():
                if mmsi in fleet:
                    fleet[mmsi][field] = value
    return fleet


def _rows(
    positions: dict[tuple[str, datetime], dict], fleet: dict[str, dict[str, str]]
) -> list[VesselPosition]:
    rows = []
    for (mmsi, hour), position in positions.items():
        known = fleet.get(mmsi)
        if known is None:
            continue
        rows.append(
            VesselPosition(
                mmsi=mmsi,
                recorded_at=hour,
                lat=position["lat"],
                lon=position["lon"],
                source=SOURCE_AIS_LIVE,
                ship_name=position.get("name") or known.get("name"),
                flag=known.get("flag"),
                gear_type=known.get("gear_type"),
                vessel_type=known.get("vessel_type"),
                effort_hours=None,
            )
        )
    return rows


def _write(
    positions: dict[tuple[str, datetime], dict], fleet: dict[str, dict[str, str]]
) -> int:
    rows = _rows(positions, fleet)
    if not rows:
        return 0
    with Session() as session:
        store.upsert(session, VesselPosition, rows, update=False)
        session.commit()
    return len(rows)


def _take_closed(
    positions: dict[tuple[str, datetime], dict], cutoff: datetime | None
) -> dict[tuple[str, datetime], dict]:
    closed = [key for key in positions if cutoff is None or key[1] < cutoff]
    return {key: positions.pop(key) for key in closed}


async def _flush(
    positions: dict[tuple[str, datetime], dict],
    fleet: dict[str, dict[str, str]],
    cutoff: datetime | None,
) -> int:
    closed = _take_closed(positions, cutoff)
    if not closed:
        return 0
    written = await asyncio.to_thread(_write, closed, fleet)
    hours = sorted({key[1] for key in closed})
    logger.info(
        "aisstream: wrote %d vessel-hours for %s",
        written,
        ", ".join(hour.isoformat() for hour in hours),
    )
    return written


async def _collect(bbox: tuple[float, float, float, float]) -> None:
    positions: dict[tuple[str, datetime], dict] = {}
    fleet = await asyncio.to_thread(_fishing_fleet)
    logger.info(
        "aisstream: %d GFW fishing vessels on the roster, writing each hour once",
        len(fleet),
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    async def consume() -> None:
        seen = unknown = 0
        async for record in stream(settings.aisstream_api_key, bbox):
            seen += 1
            if record["mmsi"] not in fleet:
                unknown += 1
                continue
            positions[(record["mmsi"], _hour(record["at"]))] = record
            if len(positions) >= MAX_BUFFER:
                await _flush(positions, fleet, None)
            if seen >= 5000:
                logger.info(
                    "aisstream: %d of %d positions off the roster, %d hours buffered",
                    unknown,
                    seen,
                    len({key[1] for key in positions}),
                )
                seen = unknown = 0

    async def write_closed_hours() -> None:
        nonlocal fleet
        last_fleet = loop.time()
        while True:
            await asyncio.sleep(WRITE_CHECK_SECONDS)
            if loop.time() - last_fleet >= FLEET_REFRESH_SECONDS:
                fleet = await asyncio.to_thread(_fishing_fleet)
                last_fleet = loop.time()
                logger.info(
                    "aisstream: roster refreshed, %d GFW fishing vessels", len(fleet)
                )
            await _flush(positions, fleet, _hour(datetime.now(timezone.utc)))

    tasks = [asyncio.create_task(consume()), asyncio.create_task(write_closed_hours())]
    stopping = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait(
            [*tasks, stopping], return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            if task is not stopping:
                task.result()
    finally:
        for task in [*tasks, stopping]:
            task.cancel()
        await asyncio.gather(*tasks, stopping, return_exceptions=True)
        written = await _flush(positions, fleet, None)
        logger.info(
            "aisstream: stopped, %d vessel-hours written on the way out", written
        )


def run(bbox: tuple[float, float, float, float]) -> None:
    asyncio.run(_collect(bbox))


def main() -> None:
    log.setup()
    if not settings.aisstream_api_key:
        raise SystemExit("AISSTREAM_API_KEY is not set")
    run(BBOX)


if __name__ == "__main__":
    main()
