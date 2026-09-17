from collections.abc import Iterator, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Date, Integer, case, distinct, func, select, tuple_
from sqlalchemy.orm import Session

from strandarr.analysis import drift, risk
from strandarr.analysis.timeframe import DayRange, midnight
from strandarr.connectors import sources
from strandarr.models import MarineCondition

BATCH_ROWS = 100_000

PRECEDENCE = case(
    {source: rank for rank, source in enumerate(sources.OBSERVED_BEFORE_PREDICTED)},
    value=MarineCondition.source,
    else_=len(sources.OBSERVED_BEFORE_PREDICTED),
)


def forcing(session: Session, day: date) -> Iterator[Sequence[Sequence[Any]]]:
    start, end = drift.window(day)
    hour = func.floor(
        func.extract("epoch", MarineCondition.valid_at - start) / 3600
    ).cast(Integer)
    statement = select(
        hour,
        PRECEDENCE,
        MarineCondition.lat,
        MarineCondition.lon,
        *(getattr(MarineCondition, name) for name in drift.MEASUREMENTS),
    ).where(MarineCondition.valid_at >= start, MarineCondition.valid_at < end)
    return iter(
        session.execute(statement.execution_options(yield_per=BATCH_ROWS)).partitions()
    )


def day_counts(
    session: Session, source: str, days: DayRange
) -> dict[date, tuple[int, int]]:
    start, end = days.bounds()
    day = func.timezone("UTC", MarineCondition.valid_at).cast(Date)
    rows = session.execute(
        select(
            day,
            func.count(),
            func.count(distinct(tuple_(MarineCondition.lat, MarineCondition.lon))),
        )
        .where(
            MarineCondition.source == source,
            MarineCondition.valid_at >= start,
            MarineCondition.valid_at < end,
        )
        .group_by(day)
    ).all()
    return {stored: (int(count), int(cells)) for stored, count, cells in rows}


def sea_state(session: Session, day: date) -> Sequence[Any]:
    seen = midnight(day - timedelta(days=risk.LAG_DAYS))
    ranked = (
        select(
            MarineCondition.lat,
            MarineCondition.lon,
            MarineCondition.swell_height_m,
            MarineCondition.wave_height_m,
            MarineCondition.wave_period_s,
            MarineCondition.wave_direction_deg,
            func.row_number()
            .over(
                partition_by=(
                    MarineCondition.lat,
                    MarineCondition.lon,
                    MarineCondition.valid_at,
                ),
                order_by=PRECEDENCE,
            )
            .label("rank"),
        )
        .where(
            MarineCondition.valid_at >= seen,
            MarineCondition.valid_at < seen + timedelta(days=1),
            MarineCondition.source.in_(sources.OBSERVED_BEFORE_PREDICTED),
        )
        .subquery()
    )
    wave = ranked.c.wave_height_m
    heading = func.radians(ranked.c.wave_direction_deg + 180.0)
    return session.execute(
        select(
            ranked.c.lat,
            ranked.c.lon,
            func.avg(ranked.c.swell_height_m),
            func.avg(wave),
            func.avg(ranked.c.wave_period_s),
            func.avg(wave * func.sin(heading)),
            func.avg(wave * func.cos(heading)),
        )
        .where(ranked.c.rank == 1)
        .group_by(ranked.c.lat, ranked.c.lon)
    ).all()


def in_hour(
    session: Session, window: tuple[datetime, datetime]
) -> Sequence[MarineCondition]:
    start, end = window
    return list(
        session.execute(
            select(MarineCondition).where(
                MarineCondition.valid_at >= start, MarineCondition.valid_at < end
            )
        ).scalars()
    )
