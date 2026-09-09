import logging

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from strandarr.models.base import utc_now
from strandarr.models.job import Job, JobStatus

logger = logging.getLogger(__name__)


def release_running(session: Session) -> int:
    """Return jobs abandoned by a killed worker to the queue. Safe only at startup:
    a running job belongs to a live worker, which this process has just replaced."""
    released = session.execute(
        update(Job)
        .where(Job.status == JobStatus.RUNNING)
        .values(status=JobStatus.PENDING, started_at=None)
    ).rowcount
    session.commit()
    if released:
        logger.warning("released %d job(s) left running by a previous worker", released)
    return released


def enqueue(session: Session, kind: str, payload: dict) -> Job:
    job = Job(kind=kind, payload=payload)
    session.add(job)
    session.flush()
    return job


def claim_next(session: Session) -> Job | None:
    stmt = (
        select(Job)
        .where(Job.status == JobStatus.PENDING)
        .order_by(Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = session.execute(stmt).scalar_one_or_none()
    if job is None:
        return None
    job.status = JobStatus.RUNNING
    job.started_at = utc_now()
    job.attempts += 1
    session.commit()
    return job


def mark_done(session: Session, job: Job) -> None:
    job.status = JobStatus.DONE
    job.finished_at = utc_now()
    session.commit()


def mark_failed(session: Session, job: Job, error: str) -> None:
    job.status = JobStatus.FAILED
    job.error = error[:2000]
    job.finished_at = utc_now()
    session.commit()
