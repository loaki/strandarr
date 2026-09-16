import logging
import time

from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.orm import Session

from strandarr import log
from strandarr.connectors.http import QuotaExhausted
from strandarr.db.engine import unit_of_work
from strandarr.jobs import queue, schedule
from strandarr.jobs.task import Payload
from strandarr.models import Job

logger = logging.getLogger(__name__)

POLL_SECONDS = 5
DB_RETRY_SECONDS = 5


def run_job(session: Session, job: Job) -> int:
    task = schedule.get(job.kind)
    rows = task.run(task.context(session), Payload.parse(job.payload))
    session.commit()
    return rows


def run_pending(session: Session) -> int:
    processed = 0
    while True:
        job = queue.claim_next(session)
        if job is None:
            return processed
        with log.job(job.id):
            logger.info("%s starting %s", job.kind, job.payload)
            started = time.monotonic()
            try:
                rows = run_job(session, job)
            except QuotaExhausted as exc:
                session.rollback()
                queue.defer(session, job, exc.retry_at, str(exc))
                logger.warning(
                    "%s hit an API quota (%s), deferred until %s",
                    job.kind,
                    exc,
                    exc.retry_at.isoformat(timespec="seconds"),
                )
                continue
            except Exception as exc:
                session.rollback()
                retry = queue.retry_or_fail(session, job, str(exc))
                logger.error(
                    "%s failed (attempt %d/%d), %s: %s",
                    job.kind,
                    job.attempts,
                    queue.MAX_ATTEMPTS,
                    "queued for retry" if retry else "giving up",
                    exc,
                )
            else:
                queue.mark_done(session, job)
                logger.info(
                    "%s done: %d rows in %.1fs",
                    job.kind,
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
                    queue.release_running(session)
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
