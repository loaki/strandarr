from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

DAY = timedelta(days=1)
CHUNK_EPOCH = date(2020, 1, 6)


def midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def day_of(at: datetime) -> date:
    return at.astimezone(UTC).date()


@dataclass(frozen=True)
class DayRange:
    start: date
    end: date

    def __bool__(self) -> bool:
        return self.start <= self.end

    def __len__(self) -> int:
        return max(0, (self.end - self.start).days + 1)

    def __iter__(self) -> Iterator[date]:
        day = self.start
        while day <= self.end:
            yield day
            day += DAY

    @classmethod
    def of(cls, day: date, days: int = 1) -> "DayRange":
        return cls(day, day + (days - 1) * DAY)

    def isoformat(self) -> tuple[str, str]:
        return self.start.isoformat(), self.end.isoformat()

    def bounds(self) -> tuple[datetime, datetime]:
        return midnight(self.start), midnight(self.end) + DAY

    def clamped(self, first: date, last: date) -> "DayRange | None":
        window = DayRange(max(self.start, first), min(self.end, last))
        return window if window else None
