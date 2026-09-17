import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, Date, func, or_, select, update
from sqlalchemy.orm import Session

from strandarr.analysis import drift, risk
from strandarr.analysis.timeframe import DayRange, day_of
from strandarr.connectors import sources
from strandarr.db.queries import coverage, environment, prediction
from strandarr.jobs import kinds
from strandarr.models import DriftRelease, IngestCoverage, MarineCondition

logger = logging.getLogger(__name__)

REBUILD_DAYS = 30

WORST_DAYS = 5

FLAG_BATCH = 500

ARCHIVES: tuple[tuple[str, str], ...] = (
    (kinds.WEATHER_ARCHIVE, sources.WEATHER_ARCHIVE),
    (kinds.MARINE_ARCHIVE, sources.MARINE_ARCHIVE),
)


@dataclass(frozen=True)
class Short:
    day: date
    row_count: int
    cell_count: int


@dataclass(frozen=True)
class Summary:
    kind: str
    days: int
    complete: int
    unmeasured: int
    expected_cells: float
    first: date | None
    last: date | None
    worst: list[Short]

    @property
    def incomplete(self) -> int:
        return self.days - self.complete


def report(session: Session) -> list[Summary]:
    rows = session.execute(
        select(
            IngestCoverage.kind,
            func.count(),
            func.count().filter(IngestCoverage.complete.is_(True)),
            func.count().filter(
                IngestCoverage.complete.is_(True),
                IngestCoverage.cell_count == 0,
                IngestCoverage.row_count == 0,
            ),
            func.min(IngestCoverage.day),
            func.max(IngestCoverage.day),
        ).group_by(IngestCoverage.kind)
    ).all()
    return [
        Summary(
            kind=kind,
            days=days,
            complete=complete,
            unmeasured=unmeasured if kind in coverage.GRIDDED_KINDS else 0,
            expected_cells=(
                coverage.expected_cells(session, kind)
                if kind in coverage.GRIDDED_KINDS
                else 0.0
            ),
            first=first,
            last=last,
            worst=_worst(session, kind),
        )
        for kind, days, complete, unmeasured, first, last in sorted(rows)
    ]


def _worst(session: Session, kind: str) -> list[Short]:
    rows = session.execute(
        select(IngestCoverage.day, IngestCoverage.row_count, IngestCoverage.cell_count)
        .where(IngestCoverage.kind == kind, IngestCoverage.complete.is_(False))
        .order_by(IngestCoverage.row_count.asc(), IngestCoverage.day.asc())
        .limit(WORST_DAYS)
    ).all()
    return [
        Short(day=day, row_count=count, cell_count=cells) for day, count, cells in rows
    ]


def rebuild(session: Session) -> list[Summary]:
    for kind, source in ARCHIVES:
        span = _stored_span(session, source)
        if span is None:
            recorded = _recorded(session, kind)
            stale = [] if recorded is None else list(recorded)
            logger.info(
                "%s: nothing stored, %d recorded day(s) reset",
                kind,
                _demote(session, kind, stale),
            )
            continue
        logger.info("%s: recounting %s..%s", kind, span.start, span.end)
        for chunk in span.chunks(REBUILD_DAYS):
            counts = environment.day_counts(session, source, chunk)
            coverage.measure(
                session,
                kind,
                {day: counts.get(day, (0, 0)) for day in chunk},
                recheck=False,
            )
            session.commit()
        moved = coverage.recalibrate(session, kind)
        session.commit()
        logger.info(
            "%s: %.0f cell(s) expected per day, %d day(s) changed state",
            kind,
            coverage.expected_cells(session, kind),
            moved,
        )

    _reflag_drift(session)
    _reflag_risk(session)
    for kind in coverage.COMPUTED_KINDS:
        coverage.release(session, kind)
    session.commit()
    return report(session)


def _stored_span(session: Session, source: str) -> DayRange | None:
    first, last = session.execute(
        select(
            func.min(MarineCondition.valid_at), func.max(MarineCondition.valid_at)
        ).where(MarineCondition.source == source)
    ).one()
    if first is None or last is None:
        return None
    return DayRange(day_of(first), day_of(last))


def _recorded(session: Session, kind: str) -> DayRange | None:
    first, last = session.execute(
        select(func.min(IngestCoverage.day), func.max(IngestCoverage.day)).where(
            IngestCoverage.kind == kind
        )
    ).one()
    return None if first is None else DayRange(first, last)


def _demote(session: Session, kind: str, days: list[date]) -> int:
    changed = 0
    for start in range(0, len(days), FLAG_BATCH):
        changed += cast(
            "CursorResult[Any]",
            session.execute(
                update(IngestCoverage)
                .where(
                    IngestCoverage.kind == kind,
                    IngestCoverage.day.in_(days[start : start + FLAG_BATCH]),
                    or_(
                        IngestCoverage.complete.is_(True),
                        IngestCoverage.checked_at.isnot(None),
                    ),
                )
                .values(complete=False, checked_at=None)
            ),
        ).rowcount
    session.commit()
    return changed


def _reflag_drift(session: Session) -> None:
    span = _recorded(session, kinds.DRIFT_ARRIVALS)
    if span is None:
        return
    forced = coverage.forced_days(session, span)
    stale = [day for day in span if day not in forced]
    changed = _demote(session, kinds.DRIFT_ARRIVALS, stale)
    session.execute(
        update(DriftRelease)
        .where(DriftRelease.model_version == drift.MODEL_VERSION)
        .values(
            complete=func.coalesce(
                select(IngestCoverage.complete)
                .where(
                    IngestCoverage.kind == kinds.DRIFT_ARRIVALS,
                    IngestCoverage.day
                    == func.timezone("UTC", DriftRelease.release_at).cast(Date),
                )
                .scalar_subquery(),
                False,
            )
        )
    )
    session.commit()
    logger.info(
        "%s: %d day(s) reset, %d of %d still have a fully archived forcing window",
        kinds.DRIFT_ARRIVALS,
        changed,
        len(forced),
        len(span),
    )


def _reflag_risk(session: Session) -> None:
    span = _recorded(session, kinds.SEGMENT_RISK)
    if span is None:
        return
    seen = timedelta(days=risk.LAG_DAYS)
    archive = coverage.archived(session, DayRange(span.start - seen, span.end - seen))
    landed = DayRange(
        span.start - timedelta(days=prediction.DRIFT_WINDOW_DAYS), span.end
    )
    simulated = coverage.simulated_days(session, landed)
    stale = [
        day
        for day in span
        if day - seen not in archive
        or any(
            day - timedelta(days=offset) not in simulated
            for offset in range(prediction.DRIFT_WINDOW_DAYS + 1)
        )
    ]
    changed = _demote(session, kinds.SEGMENT_RISK, stale)
    logger.info("%s: %d day(s) reset", kinds.SEGMENT_RISK, changed)


def stored_hours(session: Session) -> list[tuple[str, date]]:
    rows = session.execute(
        select(MarineCondition.source, func.max(MarineCondition.valid_at)).group_by(
            MarineCondition.source
        )
    ).all()
    return [(source, day_of(last)) for source, last in sorted(rows)]
