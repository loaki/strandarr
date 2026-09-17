import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from strandarr.analysis.timeframe import DayRange
from strandarr.config import settings
from strandarr.db.queries import coverage

logger = logging.getLogger(__name__)

DEFAULT_BACKFILL_DAYS = 14

ERA5_FIRST_DAY = date(1940, 1, 1)
MARINE_ARCHIVE_FIRST_DAY = date(2022, 1, 1)
GBIF_LAST_DAY = date(2022, 12, 31)
HISTOCARTO_FIRST_DAY = date(2023, 1, 1)
GFW_FIRST_DAY = date(2012, 1, 1)
GFW_LAG_DAYS = 4

ARCHIVE_LAG_DAYS = 5

FORECAST_AHEAD_DAYS = settings.marine_forecast_hours // 24

CHUNK_EPOCH = ERA5_FIRST_DAY
CHUNK_DAYS = 14


@dataclass(frozen=True)
class Window:
    first_day: date = date.min
    last_day: date = date.max
    lag_days: int = 0
    ahead_days: int = 0
    rolling: bool = False
    tracked: bool = True
    chunk_days: int = CHUNK_DAYS

    def clamp(self, days: DayRange) -> DayRange | None:
        available = date.today() + timedelta(days=self.ahead_days - self.lag_days)
        return days.clamped(self.first_day, min(self.last_day, available))


@dataclass(frozen=True)
class Payload:
    days: DayRange | None = None
    force: bool = False

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> "Payload":
        start, end = raw.get("start"), raw.get("end")
        return cls(
            days=DayRange.parse(start, end) if start and end else None,
            force=bool(raw.get("force", False)),
        )

    def dump(self) -> dict[str, Any]:
        if self.days is None:
            return {"force": self.force}
        start, end = self.days.isoformat()
        return {"start": start, "end": end, "force": self.force}

    @property
    def key(self) -> tuple[str | None, bool]:
        return self.dump().get("start"), self.force

    @property
    def span(self) -> DayRange:
        if self.days is None:
            raise ValueError("this payload carries no day range")
        return self.days


@dataclass(frozen=True)
class Context:
    session: Session
    kind: str

    def covered(self, days: DayRange, force: bool = False) -> set[date]:
        return set() if force else coverage.covered(self.session, self.kind, days)

    def settled(self, days: DayRange, force: bool = False) -> set[date]:
        return set() if force else coverage.settled(self.session, self.kind, days)

    def record(
        self,
        counts: Mapping[date, int],
        complete: bool = True,
        recheck: bool = False,
    ) -> None:
        coverage.record(self.session, self.kind, counts, complete, recheck)

    def measure(self, counts: Mapping[date, tuple[int, int]]) -> int:
        return coverage.measure(self.session, self.kind, counts)

    def commit(self) -> None:
        self.session.commit()


@dataclass(frozen=True, kw_only=True)
class Task:
    kind: str
    window: Window = field(default_factory=Window)

    def run(self, ctx: Context, payload: Payload) -> int:
        raise NotImplementedError

    def context(self, session: Session) -> Context:
        return Context(session=session, kind=self.kind)

    def days(self, payload: Payload) -> DayRange | None:
        return self.window.clamp(payload.span)

    def payloads(self, session: Session, days: DayRange, force: bool) -> list[Payload]:
        if self.window.rolling:
            return [Payload(force=force)]

        window = self.window.clamp(days)
        if window is None:
            logger.info(
                "%s: nothing to request, %s..%s is outside its coverage",
                self.kind,
                days.start,
                days.end,
            )
            return []
        if window != days:
            logger.info(
                "%s: clamped to its coverage, %s..%s",
                self.kind,
                window.start,
                window.end,
            )
        if not self.window.tracked:
            return [Payload(days=window, force=force)]

        stored = set() if force else coverage.settled(session, self.kind, window)
        if stored:
            logger.info(
                "%s: %d day(s) already settled, skipped", self.kind, len(stored)
            )
        return [
            Payload(days=chunk, force=force)
            for chunk in window.buckets(self.window.chunk_days, CHUNK_EPOCH, stored)
        ]
