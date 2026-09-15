import logging
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, or_, select, update
from sqlalchemy.orm import Session

from strandarr.models import Job, JobStatus
from strandarr.models.base import utc_now

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAY = timedelta(minutes=1)
UNFINISHED = (JobStatus.PENDING, JobStatus.RUNNING)


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


def unfinished_payloads(session: Session, kind: str) -> list[dict[str, Any]]:
    return list(
        session.execute(
            select(Job.payload).where(Job.kind == kind, Job.status.in_(UNFINISHED))
        ).scalars()
    )


def defer(session: Session, job: Job, until: datetime) -> None:
    job.status = JobStatus.PENDING
    job.attempts = max(0, job.attempts - 1)
    job.not_before = until
    session.commit()


def enqueue(session: Session, kind: str, payload: dict[str, Any]) -> Job:
    job = Job(kind=kind, payload=payload)
    session.add(job)
    session.flush()
    return job


def claim_next(session: Session) -> Job | None:
    stmt = (
        select(Job)
        .where(
            Job.status == JobStatus.PENDING,
            or_(Job.not_before.is_(None), Job.not_before <= utc_now()),
        )
        .order_by(Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = session.execute(stmt).scalar_one_or_none()
    if job is None:
        return None
    job.status = JobStatus.RUNNING
    job.started_at = utc_now()
    job.not_before = None
    job.attempts += 1
    session.commit()
    return job


def mark_done(session: Session, job: Job) -> None:
    job.status = JobStatus.DONE
    job.finished_at = utc_now()
    job.error = None
    session.commit()


def retry_or_fail(session: Session, job: Job, error: str) -> bool:
    job.error = error[:2000]
    retry = job.attempts < MAX_ATTEMPTS
    if retry:
        job.status = JobStatus.PENDING
        job.not_before = utc_now() + RETRY_DELAY
    else:
        job.status = JobStatus.FAILED
        job.finished_at = utc_now()
    session.commit()
    return retry
