import asyncio
import contextlib
import json
import logging
import signal
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import websockets
from sqlalchemy import select
from sqlalchemy.orm import InstrumentedAttribute, Session
from websockets.asyncio.client import connect

from strandarr.analysis.geo import GRID, BBox
from strandarr.config import settings
from strandarr.db import unit_of_work, upsert
from strandarr.models import VesselPosition
from strandarr.sources import AIS_LIVE, GFW_FISHING
from strandarr.sources.http import backoff

logger = logging.getLogger(__name__)

STREAM_URL = "wss://stream.aisstream.io/v0/stream"
SUBSCRIBE_SECONDS = 3
MESSAGE_TYPES = ["PositionReport"]
BACKOFF_SECONDS = 2
MAX_BACKOFF_SECONDS = 120

WRITE_CHECK_SECONDS = 60
LOG_EVERY_POSITIONS = 5000
MAX_BUFFER = 5000
FLEET_REFRESH_SECONDS = 1800

Position = dict[str, Any]
Key = tuple[str, datetime]
Fleet = dict[str, dict[str, str]]

FLEET_FIELDS: tuple[tuple[str, Any], ...] = (
    ("flag", VesselPosition.flag),
    ("gear_type", VesselPosition.gear_type),
    ("vessel_type", VesselPosition.vessel_type),
    ("name", VesselPosition.ship_name),
)


def subscription(api_key: str, bbox: BBox) -> dict[str, Any]:
    min_lon, min_lat, max_lon, max_lat = bbox.corners()
    return {
        "APIKey": api_key,
        "BoundingBoxes": [[[max_lat, min_lon], [min_lat, max_lon]]],
        "FilterMessageTypes": MESSAGE_TYPES,
    }


def _received_at(meta: dict[str, Any]) -> datetime:
    raw = str(meta.get("time_utc", ""))[:19]
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=UTC)
    except ValueError:
        return datetime.now(UTC)


def parse(message: dict[str, Any]) -> dict[str, Any] | None:
    if message.get("MessageType") != "PositionReport":
        return None
    meta = message.get("MetaData") or {}
    mmsi = meta.get("MMSI") or meta.get("MMSI_String")
    if not mmsi:
        return None
    report = (message.get("Message") or {}).get("PositionReport") or {}
    lat, lon = report.get("Latitude"), report.get("Longitude")
    if lat is None or lon is None:
        return None
    return {
        "mmsi": str(mmsi),
        "at": _received_at(meta),
        "lat": float(lat),
        "lon": float(lon),
        "name": (meta.get("ShipName") or "").strip() or None,
    }


async def stream(api_key: str, bbox: BBox) -> AsyncIterator[dict[str, Any]]:
    attempt = 0
    while True:
        try:
            async with connect(STREAM_URL, compression="deflate") as socket:
                await asyncio.wait_for(
                    socket.send(json.dumps(subscription(api_key, bbox))),
                    SUBSCRIBE_SECONDS,
                )
                logger.info("aisstream: subscribed to %s", bbox)
                attempt = 0
                async for frame in socket:
                    try:
                        message = json.loads(frame)
                    except ValueError:
                        continue
                    if message.get("Error"):
                        raise RuntimeError(
                            f"aisstream refused the subscription: {message}"
                        )
                    record = parse(message)
                    if record is not None:
                        yield record
        except asyncio.CancelledError:
            raise
        except (
            TimeoutError,
            OSError,
            websockets.WebSocketException,
            RuntimeError,
        ) as exc:
            attempt += 1
            delay = backoff(attempt, BACKOFF_SECONDS, MAX_BACKOFF_SECONDS)
            logger.warning(
                "aisstream: disconnected (%s), reconnecting in %.0fs (attempt %d)",
                exc,
                delay,
                attempt,
            )
            await asyncio.sleep(delay)


def _last_known(
    session: Session, column: InstrumentedAttribute[str | None]
) -> dict[str, str]:
    statement = (
        select(VesselPosition.mmsi, column)
        .where(VesselPosition.source == GFW_FISHING, column.isnot(None))
        .distinct(VesselPosition.mmsi)
        .order_by(VesselPosition.mmsi, VesselPosition.recorded_at.desc())
    )
    return {mmsi: value for mmsi, value in session.execute(statement).all() if value}


def fishing_fleet(session: Session) -> Fleet:
    fleet: Fleet = {
        mmsi: {}
        for mmsi in session.execute(
            select(VesselPosition.mmsi)
            .where(VesselPosition.source == GFW_FISHING)
            .distinct()
        ).scalars()
    }
    for field, column in FLEET_FIELDS:
        for mmsi, value in _last_known(session, column).items():
            if mmsi in fleet:
                fleet[mmsi][field] = value
    return fleet


def _hour(at: datetime) -> datetime:
    return at.replace(minute=0, second=0, microsecond=0)


def _roster() -> Fleet:
    with unit_of_work() as session:
        return fishing_fleet(session)


def _rows(positions: dict[Key, Position], fleet: Fleet) -> list[VesselPosition]:
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
                source=AIS_LIVE,
                ship_name=position.get("name") or known.get("name"),
                flag=known.get("flag"),
                gear_type=known.get("gear_type"),
                vessel_type=known.get("vessel_type"),
                effort_hours=None,
            )
        )
    return rows


def _write(positions: dict[Key, Position], fleet: Fleet) -> int:
    rows = _rows(positions, fleet)
    if not rows:
        return 0
    with unit_of_work() as session:
        upsert(session, VesselPosition, rows, overwrite=False)
        session.commit()
    return len(rows)


def _take_closed(
    positions: dict[Key, Position], cutoff: datetime | None
) -> dict[Key, Position]:
    closed = [key for key in positions if cutoff is None or key[1] < cutoff]
    return {key: positions.pop(key) for key in closed}


async def _flush(
    positions: dict[Key, Position], fleet: Fleet, cutoff: datetime | None
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


async def _collect(bbox: BBox) -> None:
    positions: dict[Key, Position] = {}
    fleet = await asyncio.to_thread(_roster)
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
            if seen >= LOG_EVERY_POSITIONS:
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
                fleet = await asyncio.to_thread(_roster)
                last_fleet = loop.time()
                logger.info(
                    "aisstream: roster refreshed, %d GFW fishing vessels", len(fleet)
                )
            await _flush(positions, fleet, _hour(datetime.now(UTC)))

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


def run_forever() -> None:
    if not settings.aisstream_api_key:
        raise SystemExit("AISSTREAM_API_KEY is not set")
    asyncio.run(_collect(GRID.bbox))
