import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
from strandarr.sources.http import QuotaExhausted
from strandarr.timeframe import DayRange

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
# A deferral is not a failure -- the inputs simply are not there yet -- but a day
# whose inputs never arrive must not re-queue itself for ever.
MAX_DEFERRALS = 168
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

# A chunked job is enqueued as soon as any of its days is inside the horizon, and
# it ingests only the days available when it runs. Without re-opening, the trailing
# chunk would stay partial for ever: re-open it until the whole chunk has aged past
# the lag.
ARCHIVE_VOLATILE_DAYS = meteo.DAYS_PER_REQUEST + ARCHIVE_LAG_DAYS

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
        volatile=ARCHIVE_VOLATILE_DAYS,
    ),
    "marine_archive": Spec(
        run=_archive(meteo.MARINE_ARCHIVE),
        first=MARINE_FIRST_DAY,
        lag=ARCHIVE_LAG_DAYS,
        chunk=meteo.DAYS_PER_REQUEST,
        volatile=ARCHIVE_VOLATILE_DAYS,
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
    # RETURNING, not rowcount: a multi-row INSERT with Python-side column defaults
    # goes through insertmanyvalues, which reports -1 rather than a count.
    statement = (
        insert(Job)
        .values(
            [{"kind": kind, "day": day, "status": JobStatus.PENDING} for day in days]
        )
        .on_conflict_do_nothing(index_elements=["kind", "day"])
        .returning(Job.id)
    )
    return len(session.execute(statement).all())


def reopen(session: Session, kind: str, since: date, until: date = date.max) -> int:
    return cast(
        "CursorResult[Any]",
        session.execute(
            update(Job)
            .where(
                Job.kind == kind,
                Job.day >= since,
                Job.day <= until,
                Job.status.in_((JobStatus.DONE, JobStatus.FAILED)),
            )
            .values(
                status=JobStatus.PENDING,
                attempts=0,
                deferrals=0,
                error=None,
                not_before=None,
                started_at=None,
                finished_at=None,
            )
        ),
    ).rowcount


def schedule(
    session: Session,
    start: date | None = None,
    end: date | None = None,
    redo: bool = False,
    kinds: Sequence[str] | None = None,
) -> int:
    today = date.today()
    span = DayRange(
        start or today - timedelta(days=DEFAULT_BACKFILL_DAYS),
        end or today + timedelta(days=settings.forecast_hours // 24),
    )
    if not span:
        raise ValueError(f"start {span.start} is after end {span.end}")
    for kind in kinds or ():
        get(kind)

    total = 0
    for kind, spec in TASKS.items():
        if kinds and kind not in kinds:
            continue
        buckets = spec.buckets(today, span)
        added = enqueue(session, kind, buckets)
        if redo:
            # Exactly the buckets this span covers, whatever state they are in.
            # A chunked kind stores the bucket start, which can precede span.start.
            reopened = (
                reopen(session, kind, min(buckets), max(buckets)) if buckets else 0
            )
        elif spec.volatile:
            reopened = reopen(session, kind, today - timedelta(days=spec.volatile))
        else:
            reopened = 0
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
                status=JobStatus.PENDING,
                attempts=0,
                deferrals=0,
                error=None,
                not_before=None,
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


def defer(
    session: Session, job: Job, reason: str, until: datetime | None = None
) -> bool:
    # A deferral is not an attempt: the job never got to do its work.
    job.attempts = max(0, job.attempts - 1)
    job.deferrals += 1
    if job.deferrals >= MAX_DEFERRALS:
        _finish(
            session,
            job,
            JobStatus.FAILED,
            f"still not ready after {job.deferrals} deferral(s): {reason}",
        )
        return False
    _finish(
        session, job, JobStatus.PENDING, reason, until or utc_now() + NOT_READY_DELAY
    )
    return True


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
                if defer(session, job, str(exc)):
                    logger.info(
                        "%s %s deferred (%d/%d): %s",
                        job.kind,
                        job.day,
                        job.deferrals,
                        MAX_DEFERRALS,
                        exc,
                    )
                else:
                    logger.error(
                        "%s %s never became ready after %d deferral(s), giving up: %s",
                        job.kind,
                        job.day,
                        job.deferrals,
                        exc,
                    )
            except QuotaExhausted as exc:
                session.rollback()
                defer(session, job, str(exc), exc.retry_at)
                logger.warning(
                    "%s %s hit an API quota (%s), deferred until %s",
                    job.kind,
                    job.day,
                    exc,
                    exc.retry_at.isoformat(timespec="seconds"),
                )
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
