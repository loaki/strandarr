from datetime import date, datetime, timedelta

from sqlalchemy import ColumnElement, Date, SQLColumnExpression, cast, func, select
from sqlalchemy.orm import Session


def stored_days(
    session: Session,
    when: SQLColumnExpression[datetime],
    start: date,
    end: date,
    filters: tuple[ColumnElement[bool], ...] = (),
) -> set[date]:
    day = cast(func.timezone("UTC", when), Date)
    statement = (
        select(day)
        .where(when >= start, when < end + timedelta(days=1), *filters)
        .distinct()
    )
    return set(session.execute(statement).scalars())


def missing_ranges(
    start: date, end: date, stored: set[date]
) -> list[tuple[date, date]]:
    ranges: list[tuple[date, date]] = []
    day = start
    while day <= end:
        if day in stored:
            day += timedelta(days=1)
            continue
        run_start = day
        while day <= end and day not in stored:
            day += timedelta(days=1)
        ranges.append((run_start, day - timedelta(days=1)))
    return ranges
