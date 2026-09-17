from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, and_, func, or_, select, update
from sqlalchemy.orm import Session

from strandarr.analysis.drift import MAX_DRIFT_DAYS
from strandarr.analysis.timeframe import DayRange
from strandarr.db.upsert import upsert
from strandarr.jobs import kinds
from strandarr.models import IngestCoverage
from strandarr.models.base import utc_now

HOURS_PER_DAY = 24

COMPLETE_RATIO = 0.98

RECHECK_DAYS = 7

EXPECTATION_DAYS = 30

GRIDDED_KINDS: tuple[str, ...] = (kinds.WEATHER_ARCHIVE, kinds.MARINE_ARCHIVE)

COMPUTED_KINDS: tuple[str, ...] = (kinds.DRIFT_ARRIVALS, kinds.SEGMENT_RISK)


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


def settled(session: Session, kind: str, days: DayRange) -> set[date]:
    horizon = utc_now() - timedelta(days=RECHECK_DAYS)
    return set(
        session.execute(
            select(IngestCoverage.day).where(
                IngestCoverage.kind == kind,
                IngestCoverage.day >= days.start,
                IngestCoverage.day <= days.end,
                or_(
                    IngestCoverage.complete.is_(True),
                    IngestCoverage.checked_at > horizon,
                ),
            )
        ).scalars()
    )


def incomplete(
    session: Session, kind: str, days: DayRange, produced: bool = False
) -> set[date]:
    statement = select(IngestCoverage.day).where(
        IngestCoverage.kind == kind,
        IngestCoverage.complete.is_(False),
        IngestCoverage.day >= days.start,
        IngestCoverage.day <= days.end,
    )
    if produced:
        statement = statement.where(IngestCoverage.row_count > 0)
    return set(session.execute(statement).scalars())


def record(
    session: Session,
    kind: str,
    counts: Mapping[date, int],
    complete: bool = True,
    recheck: bool = False,
) -> None:
    checked_at = utc_now() if recheck else None
    upsert(
        session,
        IngestCoverage,
        [
            IngestCoverage(
                kind=kind,
                day=day,
                row_count=count,
                cell_count=0,
                complete=complete,
                checked_at=checked_at,
            )
            for day, count in counts.items()
        ],
        overwrite=True,
    )


def stall(session: Session, kind: str, days: Iterable[date]) -> None:
    listed = list(days)
    if not listed:
        return
    now = utc_now()
    known = set(
        session.execute(
            select(IngestCoverage.day).where(
                IngestCoverage.kind == kind, IngestCoverage.day.in_(listed)
            )
        ).scalars()
    )
    session.execute(
        update(IngestCoverage)
        .where(IngestCoverage.kind == kind, IngestCoverage.day.in_(listed))
        .values(complete=False, checked_at=now)
    )
    upsert(
        session,
        IngestCoverage,
        [
            IngestCoverage(
                kind=kind,
                day=day,
                row_count=0,
                cell_count=0,
                complete=False,
                checked_at=now,
            )
            for day in listed
            if day not in known
        ],
        overwrite=True,
    )


def release(session: Session, kind: str) -> int:
    return cast(
        "CursorResult[Any]",
        session.execute(
            update(IngestCoverage)
            .where(
                IngestCoverage.kind == kind,
                IngestCoverage.complete.is_(False),
                IngestCoverage.checked_at.isnot(None),
            )
            .values(checked_at=None)
        ),
    ).rowcount


def measure(
    session: Session,
    kind: str,
    counts: Mapping[date, tuple[int, int]],
    recheck: bool = True,
) -> int:
    store(session, kind, counts, recheck)
    return recalibrate(session, kind)


def store(
    session: Session,
    kind: str,
    counts: Mapping[date, tuple[int, int]],
    recheck: bool = True,
) -> None:
    checked_at = utc_now() if recheck else None
    target = expected_cells(session, kind)
    upsert(
        session,
        IngestCoverage,
        [
            IngestCoverage(
                kind=kind,
                day=day,
                row_count=rows,
                cell_count=cells,
                complete=whole(rows, cells, target),
                checked_at=checked_at,
            )
            for day, (rows, cells) in counts.items()
        ],
        overwrite=True,
    )


def expected_cells(session: Session, kind: str) -> float:
    median, counted = session.execute(
        select(
            func.percentile_cont(0.5).within_group(IngestCoverage.cell_count.asc()),
            func.count(),
        ).where(IngestCoverage.kind == kind, IngestCoverage.cell_count > 0)
    ).one()
    if counted < EXPECTATION_DAYS:
        return 0.0
    return float(median or 0.0)


def whole(rows: int, cells: int, target: float) -> bool:
    if cells <= 0 or target <= 0:
        return False
    return (
        cells >= COMPLETE_RATIO * target
        and rows >= COMPLETE_RATIO * HOURS_PER_DAY * cells
    )


def recalibrate(session: Session, kind: str) -> int:
    target = expected_cells(session, kind)
    if target <= 0.0:
        return 0
    reached = and_(
        IngestCoverage.cell_count >= COMPLETE_RATIO * target,
        IngestCoverage.row_count
        >= COMPLETE_RATIO * HOURS_PER_DAY * IngestCoverage.cell_count,
    )
    return cast(
        "CursorResult[Any]",
        session.execute(
            update(IngestCoverage)
            .where(
                IngestCoverage.kind == kind,
                IngestCoverage.cell_count > 0,
                IngestCoverage.complete.is_distinct_from(reached),
            )
            .values(complete=reached)
        ),
    ).rowcount


def archived(session: Session, days: DayRange) -> set[date]:
    return set.intersection(*(covered(session, kind, days) for kind in GRIDDED_KINDS))


def frontier(session: Session) -> date:
    edge = date.max
    for kind in GRIDDED_KINDS:
        last = session.execute(
            select(func.max(IngestCoverage.day)).where(
                IngestCoverage.kind == kind, IngestCoverage.complete.is_(True)
            )
        ).scalar()
        if last is None:
            return date.min
        edge = min(edge, last)
    return edge


def provisional_from(session: Session) -> date:
    edge = frontier(session)
    if edge == date.min:
        return date.max
    return edge - timedelta(days=MAX_DRIFT_DAYS - 2)


def release_window(day: date) -> DayRange:
    return DayRange(day - timedelta(days=MAX_DRIFT_DAYS - 1), day)


def forcing_window(day: date) -> DayRange:
    return DayRange(day, day + timedelta(days=MAX_DRIFT_DAYS - 1))


def forced_days(session: Session, days: DayRange) -> set[date]:
    complete = archived(
        session, DayRange(days.start, days.end + timedelta(days=MAX_DRIFT_DAYS - 1))
    )
    return {
        day for day in days if all(step in complete for step in forcing_window(day))
    }


def simulated_days(session: Session, days: DayRange) -> set[date]:
    done = covered(
        session,
        kinds.DRIFT_ARRIVALS,
        DayRange(days.start - timedelta(days=MAX_DRIFT_DAYS - 1), days.end),
    )
    return {day for day in days if all(step in done for step in release_window(day))}


@dataclass(frozen=True)
class Releases:
    needed: DayRange
    missing: list[date]
    provisional: list[date]


def releases(session: Session, day: date) -> Releases:
    needed = release_window(day)
    done = covered(session, kinds.DRIFT_ARRIVALS, needed)
    partial = incomplete(session, kinds.DRIFT_ARRIVALS, needed, produced=True)
    return Releases(
        needed=needed,
        missing=sorted(set(needed) - done - partial),
        provisional=sorted(partial),
    )
