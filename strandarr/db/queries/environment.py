from collections.abc import Iterator, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Integer, case, func, select
from sqlalchemy.orm import Session

from strandarr import sources
from strandarr.analysis import conditions, drift
from strandarr.models import MarineCondition
from strandarr.timeframe import midnight

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


def sea_state(session: Session, day: date) -> Sequence[Any]:
    seen = midnight(day - timedelta(days=conditions.LAG_DAYS))
    wave = MarineCondition.wave_height_m
    heading = func.radians(MarineCondition.wave_direction_deg + 180.0)
    return session.execute(
        select(
            MarineCondition.lat,
            MarineCondition.lon,
            func.avg(MarineCondition.swell_height_m),
            func.avg(wave),
            func.avg(MarineCondition.wave_period_s),
            func.avg(wave * func.sin(heading)),
            func.avg(wave * func.cos(heading)),
        )
        .where(
            MarineCondition.valid_at >= seen,
            MarineCondition.valid_at < seen + timedelta(days=1),
            MarineCondition.source.in_(sources.OBSERVED_BEFORE_PREDICTED),
        )
        .group_by(MarineCondition.lat, MarineCondition.lon)
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
