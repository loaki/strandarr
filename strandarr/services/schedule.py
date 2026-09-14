import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, SQLColumnExpression
from sqlalchemy.orm import Session

from strandarr import sources
from strandarr.analysis import drift
from strandarr.connectors import open_meteo
from strandarr.models import DriftRelease, MarineCondition, VesselPosition
from strandarr.repositories import queue
from strandarr.services import handlers
from strandarr.services.coverage import missing_ranges, stored_days
from strandarr.services.handlers import Handler

logger = logging.getLogger(__name__)

DEFAULT_BACKFILL_DAYS = 14
GFW_LAG_DAYS = 4

ERA5_FIRST_DAY = date(1940, 1, 1)
MARINE_ARCHIVE_FIRST_DAY = date(2022, 1, 1)
GBIF_LAST_DAY = date(2022, 12, 31)
HISTOCARTO_FIRST_DAY = date(2023, 1, 1)
GFW_FIRST_DAY = date(2012, 1, 1)


@dataclass(frozen=True, eq=False)
class Source:
    kind: str
    handler: Handler
    first_day: date = date.min
    when: SQLColumnExpression[datetime] | None = None
    filters: tuple[ColumnElement[bool], ...] = field(default=())
    lag_days: int = 0
    last_day: date | None = None
    rolling: bool = False

    def window(self, start: date, end: date) -> tuple[date, date] | None:
        available = date.today() - timedelta(days=self.lag_days)
        first = max(start, self.first_day)
        last = min(end, available, self.last_day or date.max)
        return (first, last) if first <= last else None


SOURCES = (
    Source(
        kind="ingest_vessel_positions",
        handler=handlers.vessel_positions,
        first_day=GFW_FIRST_DAY,
        when=VesselPosition.recorded_at,
        filters=(VesselPosition.source == sources.GFW_FISHING,),
        lag_days=GFW_LAG_DAYS,
    ),
    Source(
        kind="ingest_weather_archive",
        handler=handlers.archive(open_meteo.WEATHER_ARCHIVE),
        first_day=ERA5_FIRST_DAY,
        when=MarineCondition.valid_at,
        filters=(MarineCondition.source == sources.WEATHER_ARCHIVE,),
    ),
    Source(
        kind="ingest_marine_archive",
        handler=handlers.archive(open_meteo.MARINE_ARCHIVE),
        first_day=MARINE_ARCHIVE_FIRST_DAY,
        when=MarineCondition.valid_at,
        filters=(MarineCondition.source == sources.MARINE_ARCHIVE,),
    ),
    Source(
        kind="ingest_strandings_gbif",
        handler=handlers.strandings_gbif,
        last_day=GBIF_LAST_DAY,
    ),
    Source(
        kind="ingest_strandings_histocarto",
        handler=handlers.strandings_histocarto,
        first_day=HISTOCARTO_FIRST_DAY,
    ),
    Source(
        kind="ingest_forecast",
        handler=handlers.forecast,
        rolling=True,
    ),
    Source(
        kind="compute_drift_arrivals",
        handler=handlers.drift_arrivals,
        first_day=MARINE_ARCHIVE_FIRST_DAY,
        when=DriftRelease.release_at,
        filters=(
            DriftRelease.complete.is_(True),
            DriftRelease.model_version == drift.MODEL_VERSION,
        ),
        lag_days=GFW_LAG_DAYS,
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
                "%s: %d range(s) already queued, not enqueued again",
                source.kind,
                skipped,
            )

    session.commit()
    logger.info("enqueued %d jobs for %s..%s", count, window_start, window_end)
    return count


def _window_key(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    return payload.get("start"), payload.get("end")


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
    return [
        {
            "start": range_start.isoformat(),
            "end": range_end.isoformat(),
            "force": force,
        }
        for range_start, range_end in _ranges(
            session, source, source_start, source_end, force
        )
    ]


def _ranges(
    session: Session, source: Source, start: date, end: date, force: bool
) -> list[tuple[date, date]]:
    if force or source.when is None:
        return [(start, end)]
    stored = stored_days(session, source.when, start, end, source.filters)
    ranges = missing_ranges(start, end, stored)
    skipped = (
        (end - start).days + 1 - sum((last - first).days + 1 for first, last in ranges)
    )
    if skipped:
        logger.info("%s: %d day(s) already stored, skipped", source.kind, skipped)
    return ranges
