from collections.abc import Iterator, Mapping
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from strandarr.models import IngestCoverage
from strandarr.repositories import store


def days(start: date, end: date) -> Iterator[date]:
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def covered_days(session: Session, kind: str, start: date, end: date) -> set[date]:
    statement = select(IngestCoverage.day).where(
        IngestCoverage.kind == kind,
        IngestCoverage.complete.is_(True),
        IngestCoverage.day >= start,
        IngestCoverage.day <= end,
    )
    return set(session.execute(statement).scalars())


def _write(session: Session, rows: list[IngestCoverage]) -> None:
    store.upsert(session, IngestCoverage, rows, overwrite=True)


def record(session: Session, kind: str, rows: Mapping[date, int]) -> None:
    _write(
        session,
        [
            IngestCoverage(kind=kind, day=day, row_count=count, complete=count > 0)
            for day, count in rows.items()
        ],
    )


def record_day(
    session: Session, kind: str, day: date, row_count: int, complete: bool
) -> None:
    _write(
        session,
        [IngestCoverage(kind=kind, day=day, row_count=row_count, complete=complete)],
    )


def missing_ranges(
    start: date, end: date, covered: set[date]
) -> list[tuple[date, date]]:
    ranges: list[tuple[date, date]] = []
    day = start
    while day <= end:
        if day in covered:
            day += timedelta(days=1)
            continue
        run_start = day
        while day <= end and day not in covered:
            day += timedelta(days=1)
        ranges.append((run_start, day - timedelta(days=1)))
    return ranges
