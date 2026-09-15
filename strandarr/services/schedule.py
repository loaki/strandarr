import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from strandarr import kinds
from strandarr.connectors import open_meteo
from strandarr.repositories import queue
from strandarr.services import coverage, handlers
from strandarr.services.handlers import Handler

logger = logging.getLogger(__name__)

DEFAULT_BACKFILL_DAYS = 14
GFW_LAG_DAYS = 4

ERA5_FIRST_DAY = date(1940, 1, 1)
MARINE_ARCHIVE_FIRST_DAY = date(2022, 1, 1)
GBIF_LAST_DAY = date(2022, 12, 31)
HISTOCARTO_FIRST_DAY = date(2023, 1, 1)
GFW_FIRST_DAY = date(2012, 1, 1)

CHUNK_EPOCH = ERA5_FIRST_DAY
CHUNK_DAYS = open_meteo.DAYS_PER_REQUEST


@dataclass(frozen=True, eq=False)
class Source:
    kind: str
    handler: Handler
    first_day: date = date.min
    lag_days: int = 0
    last_day: date | None = None
    rolling: bool = False
    tracked: bool = True

    def window(self, start: date, end: date) -> tuple[date, date] | None:
        available = date.today() - timedelta(days=self.lag_days)
        first = max(start, self.first_day)
        last = min(end, available, self.last_day or date.max)
        return (first, last) if first <= last else None


SOURCES = (
    Source(
        kind=kinds.VESSEL_POSITIONS,
        handler=handlers.vessel_positions,
        first_day=GFW_FIRST_DAY,
        lag_days=GFW_LAG_DAYS,
    ),
    Source(
        kind=kinds.WEATHER_ARCHIVE,
        handler=handlers.archive(open_meteo.WEATHER_ARCHIVE),
        first_day=ERA5_FIRST_DAY,
    ),
    Source(
        kind=kinds.MARINE_ARCHIVE,
        handler=handlers.archive(open_meteo.MARINE_ARCHIVE),
        first_day=MARINE_ARCHIVE_FIRST_DAY,
    ),
    Source(
        kind=kinds.STRANDINGS_GBIF,
        handler=handlers.strandings_gbif,
        last_day=GBIF_LAST_DAY,
        tracked=False,
    ),
    Source(
        kind=kinds.STRANDINGS_HISTOCARTO,
        handler=handlers.strandings_histocarto,
        first_day=HISTOCARTO_FIRST_DAY,
        tracked=False,
    ),
    Source(
        kind=kinds.FORECAST,
        handler=handlers.forecast,
        rolling=True,
    ),
    Source(
        kind=kinds.DRIFT_ARRIVALS,
        handler=handlers.drift_arrivals,
        first_day=MARINE_ARCHIVE_FIRST_DAY,
        lag_days=GFW_LAG_DAYS,
    ),
    Source(
        kind=kinds.CLIMATOLOGY,
        handler=handlers.climatology,
        rolling=True,
    ),
)

BY_KIND = {source.kind: source for source in SOURCES}


def schedule_missing(
    session: Session,
    start: date | None = None,
    end: date | None = None,
    force: bool = False,
) -> int:
    window_end = end or date.today()
    window_start = start or window_end - timedelta(days=DEFAULT_BACKFILL_DAYS)
    if window_start > window_end:
        raise ValueError(f"start {window_start} is after end {window_end}")

    count = 0
    for source in SOURCES:
        queued = {
            _window_key(payload)
            for payload in queue.unfinished_payloads(session, source.kind)
        }
        skipped = 0
        for payload in _payloads(session, source, window_start, window_end, force):
            if _window_key(payload) in queued:
                skipped += 1
                continue
            queue.enqueue(session, source.kind, payload)
            count += 1
        if skipped:
            logger.info(
                "%s: %d chunk(s) already queued, not enqueued again",
                source.kind,
                skipped,
            )

    session.commit()
    logger.info("enqueued %d jobs for %s..%s", count, window_start, window_end)
    return count


def _window_key(payload: dict[str, Any]) -> tuple[str | None, str | None, bool]:
    return payload.get("start"), payload.get("end"), bool(payload.get("force"))


def _payload(start: date, end: date, force: bool) -> dict[str, Any]:
    return {"start": start.isoformat(), "end": end.isoformat(), "force": force}


def _payloads(
    session: Session,
    source: Source,
    window_start: date,
    window_end: date,
    force: bool,
) -> list[dict[str, Any]]:
    if source.rolling:
        return [{"force": force}]

    window = source.window(window_start, window_end)
    if window is None:
        logger.info(
            "%s: nothing to request, %s..%s is outside its coverage",
            source.kind,
            window_start,
            window_end,
        )
        return []
    source_start, source_end = window
    if window != (window_start, window_end):
        logger.info(
            "%s: clamped to its coverage, %s..%s", source.kind, source_start, source_end
        )
    if not source.tracked:
        return [_payload(source_start, source_end, force)]

    covered = (
        set()
        if force
        else coverage.covered_days(session, source.kind, source_start, source_end)
    )
    if covered:
        logger.info("%s: %d day(s) already covered, skipped", source.kind, len(covered))
    return [
        _payload(first, last, force)
        for first, last in _chunks(source_start, source_end, covered)
    ]


def _chunk(day: date) -> tuple[date, date]:
    bucket = (day - CHUNK_EPOCH).days // CHUNK_DAYS
    first = CHUNK_EPOCH + timedelta(days=bucket * CHUNK_DAYS)
    return first, first + timedelta(days=CHUNK_DAYS - 1)


def _chunks(start: date, end: date, covered: set[date]) -> list[tuple[date, date]]:
    spans = {_chunk(day) for day in coverage.days(start, end) if day not in covered}
    return [(max(first, start), min(last, end)) for first, last in sorted(spans)]
