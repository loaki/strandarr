from collections.abc import Mapping
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from strandarr.analysis.drift import MAX_DRIFT_DAYS
from strandarr.analysis.timeframe import DayRange
from strandarr.db.upsert import upsert
from strandarr.jobs import kinds
from strandarr.models import IngestCoverage


def covered(session: Session, kind: str, days: DayRange) -> set[date]:
    return set(
        session.execute(
            select(IngestCoverage.day).where(
                IngestCoverage.kind == kind,
                IngestCoverage.complete.is_(True),
                IngestCoverage.day >= days.start,
                IngestCoverage.day <= days.end,
            )
        ).scalars()
    )


def record(
    session: Session,
    kind: str,
    counts: Mapping[date, int],
    complete: bool = True,
) -> None:
    upsert(
        session,
        IngestCoverage,
        [
            IngestCoverage(kind=kind, day=day, row_count=count, complete=complete)
            for day, count in counts.items()
        ],
        overwrite=True,
    )


def release_window(day: date) -> DayRange:
    return DayRange(day - timedelta(days=MAX_DRIFT_DAYS - 1), day)


def missing_releases(session: Session, day: date) -> tuple[DayRange, list[date]]:
    needed = release_window(day)
    return needed, sorted(set(needed) - covered(session, kinds.DRIFT_ARRIVALS, needed))


def eligible_days(session: Session, days: DayRange, minimum: int) -> set[date]:
    simulated = covered(
        session,
        kinds.DRIFT_ARRIVALS,
        DayRange(days.start - timedelta(days=MAX_DRIFT_DAYS - 1), days.end),
    )
    return {
        day
        for day in days
        if sum(
            day - timedelta(days=offset) in simulated
            for offset in range(MAX_DRIFT_DAYS)
        )
        >= minimum
    }
