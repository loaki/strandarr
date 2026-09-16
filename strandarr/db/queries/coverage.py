from collections.abc import Mapping
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from strandarr.db.upsert import upsert
from strandarr.models import IngestCoverage
from strandarr.timeframe import DayRange


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
