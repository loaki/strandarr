import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, case, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.orm import Session

from strandarr import log
from strandarr.analysis import drift, risk
from strandarr.analysis.geo import Point, grid_points, load_segments
from strandarr.config import settings
from strandarr.db import unit_of_work
from strandarr.errors import NotReady
from strandarr.models import Job, JobStatus, utc_now
from strandarr.sources import gfw, meteo, strandings
from strandarr.timeframe import DayRange

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAY = timedelta(minutes=1)
NOT_READY_DELAY = timedelta(hours=1)
POLL_SECONDS = 5
DB_RETRY_SECONDS = 5

DEFAULT_BACKFILL_DAYS = 14

CHUNK_EPOCH = date(2020, 1, 6)

ERA5_FIRST_DAY = date(1940, 1, 1)
MARINE_FIRST_DAY = date(2022, 1, 1)
GBIF_LAST_DAY = date(2022, 12, 31)
PELAGIS_FIRST_DAY = date(2023, 1, 1)
GFW_FIRST_DAY = date(2012, 1, 1)

GFW_LAG_DAYS = 4
ARCHIVE_LAG_DAYS = 5

Handler = Callable[[Session, DayRange], int]


@dataclass(frozen=True)
class Spec:
    run: Handler
    first: date = date.min
    last: date = date.max
    lag: int = 0
    ahead: int = 0
    chunk: int = 1
    volatile: int = 0
    rolling: bool = False

    def horizon(self, today: date) -> date:
        return min(self.last, today + timedelta(days=self.ahead - self.lag))

    def window(self, today: date, span: DayRange) -> DayRange | None:
        return span.clamped(self.first, self.horizon(today))

    def buckets(self, today: date, span: DayRange) -> list[date]:
        if self.rolling:
            return [today]
        window = self.window(today, span)
        if window is None:
            return []
        return sorted(
            {
                CHUNK_EPOCH
                + timedelta(days=(day - CHUNK_EPOCH).days // self.chunk * self.chunk)
                for day in window
            }
        )

    def days(self, today: date, bucket: date) -> DayRange | None:
        if self.rolling:
            return DayRange.of(bucket)
        return self.window(today, DayRange.of(bucket, self.chunk))


def _per_day(task: Callable[[Session, date], int]) -> Handler:
    return lambda session, days: sum(task(session, day) for day in days)


def _points(session: Session) -> list[Point]:
    return grid_points(load_segments(session))


def _archive(product: meteo.Product) -> Handler:
    return lambda session, days: meteo.archive(session, product, _points(session), days)


def _forecast(session: Session, days: DayRange) -> int:
    return meteo.forecast(session, _points(session))


TASKS: dict[str, Spec] = {
    "gfw": Spec(
        run=lambda session, days: gfw.ingest(session, days.start),
        first=GFW_FIRST_DAY,
        lag=GFW_LAG_DAYS,
    ),
    "weather_archive": Spec(
        run=_archive(meteo.WEATHER_ARCHIVE),
        first=ERA5_FIRST_DAY,
        lag=ARCHIVE_LAG_DAYS,
        chunk=meteo.DAYS_PER_REQUEST,
    ),
    "marine_archive": Spec(
        run=_archive(meteo.MARINE_ARCHIVE),
        first=MARINE_FIRST_DAY,
        lag=ARCHIVE_LAG_DAYS,
        chunk=meteo.DAYS_PER_REQUEST,
    ),
    "forecast": Spec(run=_forecast, rolling=True, volatile=1),
    "strandings_gbif": Spec(
        run=lambda session, days: strandings.ingest_gbif(session, days),
        last=GBIF_LAST_DAY,
        chunk=365,
    ),
    "strandings_pelagis": Spec(
        run=lambda session, days: strandings.ingest_pelagis(session, days),
        first=PELAGIS_FIRST_DAY,
        chunk=30,
        volatile=60,
    ),
    "drift": Spec(
        run=_per_day(drift.run),
        first=MARINE_FIRST_DAY,
        lag=GFW_LAG_DAYS,
        volatile=drift.MAX_DRIFT_DAYS,
    ),
    "risk": Spec(
        run=_per_day(risk.run),
        first=MARINE_FIRST_DAY,
        ahead=settings.forecast_hours // 24,
        volatile=risk.MAX_LOOKBACK_DAYS,
    ),
}

ORDER = case(
    {kind: position for position, kind in enumerate(TASKS)},
    value=Job.kind,
    else_=len(TASKS),
)


def get(kind: str) -> Spec:
    spec = TASKS.get(kind)
    if spec is None:
        raise ValueError(f"unknown job kind: {kind}")
    return spec


def enqueue(session: Session, kind: str, days: list[date]) -> int:
    if not days:
        return 0
    statement = (
        insert(Job)
        .values(
            [{"kind": kind, "day": day, "status": JobStatus.PENDING} for day in days]
        )
        .on_conflict_do_nothing(index_elements=["kind", "day"])
    )
    return cast("CursorResult[Any]", session.execute(statement)).rowcount


def reopen(session: Session, kind: str, since: date) -> int:
    return cast(
        "CursorResult[Any]",
        session.execute(
            update(Job)
            .where(
                Job.kind == kind,
                Job.day >= since,
                Job.status.in_((JobStatus.DONE, JobStatus.FAILED)),
            )
            .values(
                status=JobStatus.PENDING,
                attempts=0,
                error=None,
                not_before=None,
                started_at=None,
                finished_at=None,
            )
        ),
    ).rowcount


def schedule(
    session: Session, start: date | None = None, end: date | None = None
) -> int:
    today = date.today()
    span = DayRange(
        start or today - timedelta(days=DEFAULT_BACKFILL_DAYS),
        end or today + timedelta(days=settings.forecast_hours // 24),
    )
    if not span:
        raise ValueError(f"start {span.start} is after end {span.end}")

    total = 0
    for kind, spec in TASKS.items():
        added = enqueue(session, kind, spec.buckets(today, span))
        reopened = (
            reopen(session, kind, today - timedelta(days=spec.volatile))
            if spec.volatile
            else 0
        )
        total += added + reopened
        if added or reopened:
            logger.info("%s: %d new, %d re-opened", kind, added, reopened)
    session.commit()
    logger.info("scheduled %d job(s) over %s..%s", total, span.start, span.end)
    return total


def retry(session: Session, kind: str | None = None) -> int:
    statement = update(Job).where(Job.status == JobStatus.FAILED)
    if kind is not None:
        statement = statement.where(Job.kind == kind)
    count = cast(
        "CursorResult[Any]",
        session.execute(
            statement.values(
                status=JobStatus.PENDING, attempts=0, error=None, not_before=None
            )
        ),
    ).rowcount
    session.commit()
    logger.info("re-queued %d failed job(s)", count)
    return count


def release_running(session: Session) -> None:
    released = cast(
        "CursorResult[Any]",
        session.execute(
            update(Job)
            .where(Job.status == JobStatus.RUNNING)
            .values(status=JobStatus.PENDING, started_at=None, not_before=None)
        ),
    ).rowcount
    session.commit()
    if released:
        logger.warning("released %d job(s) left running by a previous worker", released)


def claim_next(session: Session) -> Job | None:
    job = session.execute(
        select(Job)
        .where(
            Job.status == JobStatus.PENDING,
            or_(Job.not_before.is_(None), Job.not_before <= utc_now()),
        )
        .order_by(Job.day.asc(), ORDER.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if job is None:
        return None
    job.status = JobStatus.RUNNING
    job.started_at = utc_now()
    job.not_before = None
    job.attempts += 1
    session.commit()
    return job


def _finish(
    session: Session,
    job: Job,
    status: JobStatus,
    error: str | None = None,
    not_before: Any = None,
) -> None:
    job.status = status
    job.error = error[:2000] if error else None
    job.not_before = not_before
    if status in (JobStatus.DONE, JobStatus.FAILED):
        job.finished_at = utc_now()
    session.commit()


def defer(session: Session, job: Job, reason: str) -> None:
    job.attempts = max(0, job.attempts - 1)
    _finish(session, job, JobStatus.PENDING, reason, utc_now() + NOT_READY_DELAY)


def retry_or_fail(session: Session, job: Job, error: str) -> bool:
    retry_it = job.attempts < MAX_ATTEMPTS
    _finish(
        session,
        job,
        JobStatus.PENDING if retry_it else JobStatus.FAILED,
        error,
        utc_now() + RETRY_DELAY if retry_it else None,
    )
    return retry_it


def run_job(session: Session, job: Job) -> int:
    spec = get(job.kind)
    days = spec.days(date.today(), job.day)
    if days is None:
        logger.info("%s %s: outside its window now, nothing to do", job.kind, job.day)
        return 0
    rows = spec.run(session, days)
    session.commit()
    return rows


def run_pending(session: Session) -> int:
    processed = 0
    while True:
        job = claim_next(session)
        if job is None:
            return processed
        with log.job(job.id):
            logger.info("%s %s starting", job.kind, job.day)
            started = time.monotonic()
            try:
                rows = run_job(session, job)
            except NotReady as exc:
                session.rollback()
                defer(session, job, str(exc))
                logger.info("%s %s deferred: %s", job.kind, job.day, exc)
            except Exception as exc:
                session.rollback()
                again = retry_or_fail(session, job, str(exc))
                logger.error(
                    "%s %s failed (attempt %d/%d), %s: %s",
                    job.kind,
                    job.day,
                    job.attempts,
                    MAX_ATTEMPTS,
                    "queued for retry" if again else "giving up",
                    exc,
                )
            else:
                _finish(session, job, JobStatus.DONE)
                logger.info(
                    "%s %s done: %d rows in %.1fs",
                    job.kind,
                    job.day,
                    rows,
                    time.monotonic() - started,
                )
        processed += 1


def run_forever() -> None:
    logger.info("worker started, polling every %ds", POLL_SECONDS)
    reconnected = True
    while True:
        try:
            with unit_of_work() as session:
                if reconnected:
                    release_running(session)
                    reconnected = False
                processed = run_pending(session)
        except (OperationalError, InterfaceError) as exc:
            reconnected = True
            logger.warning(
                "database unavailable (%s), retrying in %ds",
                exc.orig or exc,
                DB_RETRY_SECONDS,
            )
            time.sleep(DB_RETRY_SECONDS)
            continue
        if not processed:
            time.sleep(POLL_SECONDS)
