from datetime import date, timedelta

from sqlalchemy.orm import Session

from strandarr import kinds
from strandarr.analysis.drift import MAX_DRIFT_DAYS
from strandarr.db.queries import coverage
from strandarr.timeframe import DayRange


def release_window(day: date) -> DayRange:
    return DayRange(day - timedelta(days=MAX_DRIFT_DAYS - 1), day)


def simulated_releases(session: Session, days: DayRange) -> set[date]:
    return coverage.covered(session, kinds.DRIFT_ARRIVALS, days)


def missing_releases(session: Session, day: date) -> tuple[DayRange, list[date]]:
    needed = release_window(day)
    simulated = simulated_releases(session, needed)
    return needed, sorted(set(needed) - simulated)


def eligible_days(session: Session, days: DayRange, minimum: int) -> set[date]:
    simulated = simulated_releases(
        session, DayRange(days.start - timedelta(days=MAX_DRIFT_DAYS - 1), days.end)
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
