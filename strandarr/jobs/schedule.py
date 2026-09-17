import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from strandarr.analysis.timeframe import DayRange
from strandarr.connectors import gbif, open_meteo, pelagis_histocarto
from strandarr.jobs import kinds, queue
from strandarr.jobs.task import (
    ARCHIVE_LAG_DAYS,
    DEFAULT_BACKFILL_DAYS,
    ERA5_FIRST_DAY,
    FORECAST_AHEAD_DAYS,
    GBIF_LAST_DAY,
    GFW_FIRST_DAY,
    GFW_LAG_DAYS,
    HISTOCARTO_FIRST_DAY,
    MARINE_ARCHIVE_FIRST_DAY,
    Payload,
    Task,
    Window,
)
from strandarr.services.drift import DriftArrivals
from strandarr.services.ingest import (
    ArchiveIngest,
    ForecastIngest,
    StrandingIngest,
    VesselIngest,
)
from strandarr.services.risk import RiskZones

logger = logging.getLogger(__name__)

TASKS: tuple[Task, ...] = (
    VesselIngest(
        kind=kinds.VESSEL_POSITIONS,
        window=Window(first_day=GFW_FIRST_DAY, lag_days=GFW_LAG_DAYS),
    ),
    ArchiveIngest(
        kind=kinds.WEATHER_ARCHIVE,
        window=Window(first_day=ERA5_FIRST_DAY, lag_days=ARCHIVE_LAG_DAYS),
        product=open_meteo.WEATHER_ARCHIVE,
    ),
    ArchiveIngest(
        kind=kinds.MARINE_ARCHIVE,
        window=Window(first_day=MARINE_ARCHIVE_FIRST_DAY, lag_days=ARCHIVE_LAG_DAYS),
        product=open_meteo.MARINE_ARCHIVE,
    ),
    StrandingIngest(
        kind=kinds.STRANDINGS_GBIF,
        window=Window(last_day=GBIF_LAST_DAY, tracked=False),
        fetch=gbif.fetch_strandings,
    ),
    StrandingIngest(
        kind=kinds.STRANDINGS_HISTOCARTO,
        window=Window(first_day=HISTOCARTO_FIRST_DAY, tracked=False),
        fetch=pelagis_histocarto.fetch_strandings,
    ),
    ForecastIngest(kind=kinds.FORECAST, window=Window(rolling=True)),
    DriftArrivals(
        kind=kinds.DRIFT_ARRIVALS,
        window=Window(first_day=MARINE_ARCHIVE_FIRST_DAY, lag_days=GFW_LAG_DAYS),
    ),
    RiskZones(
        kind=kinds.SEGMENT_RISK,
        window=Window(
            first_day=MARINE_ARCHIVE_FIRST_DAY, ahead_days=FORECAST_AHEAD_DAYS
        ),
    ),
)

BY_KIND: dict[str, Task] = {task.kind: task for task in TASKS}


def get(kind: str) -> Task:
    task = BY_KIND.get(kind)
    if task is None:
        raise ValueError(f"unknown job kind: {kind}")
    return task


def schedule_missing(
    session: Session,
    start: date | None = None,
    end: date | None = None,
    force: bool = False,
) -> int:
    last = end or date.today() + timedelta(days=FORECAST_AHEAD_DAYS)
    first = start or date.today() - timedelta(days=DEFAULT_BACKFILL_DAYS)
    days = DayRange(first, last)
    if not days:
        raise ValueError(f"start {first} is after end {last}")

    planned: list[tuple[int, int, str, Payload]] = []
    for order, task in enumerate(TASKS):
        queued = queue.queued_keys(session, task.kind)
        skipped = 0
        for payload in task.payloads(session, days, force):
            if payload.key in queued:
                skipped += 1
                continue
            reach = last if payload.days is None else payload.days.end
            planned.append((-reach.toordinal(), order, task.kind, payload))
        if skipped:
            logger.info(
                "%s: %d chunk(s) already queued, not enqueued again",
                task.kind,
                skipped,
            )

    planned.sort(key=lambda item: item[:2])
    for _, _, kind, payload in planned:
        queue.enqueue(session, kind, payload)

    session.commit()
    logger.info("enqueued %d jobs for %s..%s", len(planned), first, last)
    return len(planned)
