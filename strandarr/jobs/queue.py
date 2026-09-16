import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from strandarr.db.queries import jobs
from strandarr.jobs.task import Payload
from strandarr.models import Job, JobStatus
from strandarr.models.base import utc_now

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAY = timedelta(minutes=1)


def release_running(session: Session) -> None:
    released = jobs.release_running(session)
    if released:
        logger.warning("released %d job(s) left running by a previous worker", released)


def queued_keys(
    session: Session, kind: str
) -> set[tuple[str | None, str | None, bool]]:
    return {Payload.parse(raw).key for raw in jobs.unfinished_payloads(session, kind)}


def enqueue(session: Session, kind: str, payload: Payload) -> Job:
    return jobs.insert(session, kind, payload.dump())


def claim_next(session: Session) -> Job | None:
    return jobs.take_pending(session)


def mark_done(session: Session, job: Job) -> None:
    jobs.finish(session, job, JobStatus.DONE)


def defer(session: Session, job: Job, until: datetime, reason: str) -> None:
    job.attempts = max(0, job.attempts - 1)
    jobs.finish(session, job, JobStatus.PENDING, reason, not_before=until)


def retry_or_fail(session: Session, job: Job, error: str) -> bool:
    retry = job.attempts < MAX_ATTEMPTS
    if retry:
        jobs.finish(
            session, job, JobStatus.PENDING, error, not_before=utc_now() + RETRY_DELAY
        )
    else:
        jobs.finish(session, job, JobStatus.FAILED, error)
    return retry
