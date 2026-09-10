import asyncio
import json
import logging
import random
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import websockets
from websockets.asyncio.client import connect

logger = logging.getLogger(__name__)

STREAM_URL = "wss://stream.aisstream.io/v0/stream"

SUBSCRIBE_SECONDS = 3

MESSAGE_TYPES = ["PositionReport"]

BACKOFF_SECONDS = 2
MAX_BACKOFF_SECONDS = 120


def subscription(api_key: str, bbox: tuple[float, float, float, float]) -> dict:
    """aisstream takes corners as [lat, lon] pairs, north-west corner first."""
    min_lon, min_lat, max_lon, max_lat = bbox
    return {
        "APIKey": api_key,
        "BoundingBoxes": [[[max_lat, min_lon], [min_lat, max_lon]]],
        "FilterMessageTypes": MESSAGE_TYPES,
    }


def _received_at(meta: dict) -> datetime:
    """MetaData.time_utc uses Go's default layout, "2026-09-10 12:00:00.000000000 +0000 UTC".
    Always UTC, so the offset and the trailing zone name carry nothing we need."""
    raw = str(meta.get("time_utc", ""))[:19]
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def parse(message: dict) -> dict | None:
    """One position report, or None if the frame carries no usable position."""
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


async def stream(
    api_key: str, bbox: tuple[float, float, float, float]
) -> AsyncIterator[dict]:
    """Position reports from the live feed, reconnecting for as long as the caller iterates.

    The stream is push-only and has no history endpoint: it can never be asked about a past
    hour, so a dropped connection is a permanent hole in coverage. Hence reconnect forever
    rather than surfacing the error.
    """
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
            OSError,
            websockets.WebSocketException,
            asyncio.TimeoutError,
            RuntimeError,
        ) as exc:
            attempt += 1
            delay = min(BACKOFF_SECONDS * 2 ** (attempt - 1), MAX_BACKOFF_SECONDS)
            delay *= 0.5 + random.random() / 2
            logger.warning(
                "aisstream: disconnected (%s), reconnecting in %.0fs (attempt %d)",
                exc,
                delay,
                attempt,
            )
            await asyncio.sleep(delay)
