import logging
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, or_, select, update
from sqlalchemy.orm import Session

from strandarr.jobs.task import Payload
from strandarr.models import Job, JobStatus
from strandarr.models.base import utc_now

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAY = timedelta(minutes=1)
UNFINISHED = (JobStatus.PENDING, JobStatus.RUNNING)

Key = tuple[str | None, bool]


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


def queued_keys(session: Session, kind: str) -> set[Key]:
    raw = session.execute(
        select(Job.payload).where(Job.kind == kind, Job.status.in_(UNFINISHED))
    ).scalars()
    return {Payload.parse(payload).key for payload in raw}


def enqueue(session: Session, kind: str, payload: Payload) -> Job:
    job = Job(kind=kind, payload=payload.dump())
    session.add(job)
    session.flush()
    return job


def claim_next(session: Session) -> Job | None:
    job = session.execute(
        select(Job)
        .where(
            Job.status == JobStatus.PENDING,
            or_(Job.not_before.is_(None), Job.not_before <= utc_now()),
        )
        .order_by(Job.created_at)
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
    not_before: datetime | None = None,
) -> None:
    job.status = status
    job.error = error[:2000] if error else None
    job.not_before = not_before
    if status in (JobStatus.DONE, JobStatus.FAILED):
        job.finished_at = utc_now()
    session.commit()


def mark_done(session: Session, job: Job) -> None:
    _finish(session, job, JobStatus.DONE)


def defer(session: Session, job: Job, until: datetime, reason: str) -> None:
    job.attempts = max(0, job.attempts - 1)
    _finish(session, job, JobStatus.PENDING, reason, not_before=until)


def retry_or_fail(session: Session, job: Job, error: str) -> bool:
    retry = job.attempts < MAX_ATTEMPTS
    if retry:
        _finish(
            session, job, JobStatus.PENDING, error, not_before=utc_now() + RETRY_DELAY
        )
    else:
        _finish(session, job, JobStatus.FAILED, error)
    return retry
