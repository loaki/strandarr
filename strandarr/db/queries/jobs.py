from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, or_, select, update
from sqlalchemy.orm import Session

from strandarr.models import Job, JobStatus
from strandarr.models.base import utc_now

UNFINISHED = (JobStatus.PENDING, JobStatus.RUNNING)


def release_running(session: Session) -> int:
    released = cast(
        "CursorResult[Any]",
        session.execute(
            update(Job)
            .where(Job.status == JobStatus.RUNNING)
            .values(status=JobStatus.PENDING, started_at=None, not_before=None)
        ),
    ).rowcount
    session.commit()
    return int(released)


def unfinished_payloads(session: Session, kind: str) -> Sequence[dict[str, Any]]:
    return list(
        session.execute(
            select(Job.payload).where(Job.kind == kind, Job.status.in_(UNFINISHED))
        ).scalars()
    )


def insert(session: Session, kind: str, payload: dict[str, Any]) -> Job:
    job = Job(kind=kind, payload=payload)
    session.add(job)
    session.flush()
    return job


def take_pending(session: Session) -> Job | None:
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


def finish(
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
