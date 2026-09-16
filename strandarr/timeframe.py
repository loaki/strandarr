from collections.abc import Container, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

DAY = timedelta(days=1)


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

    def __contains__(self, day: object) -> bool:
        return isinstance(day, date) and self.start <= day <= self.end

    @classmethod
    def parse(cls, start: str, end: str) -> "DayRange":
        return cls(date.fromisoformat(start), date.fromisoformat(end))

    def isoformat(self) -> tuple[str, str]:
        return self.start.isoformat(), self.end.isoformat()

    def clamped(
        self, first: date = date.min, last: date = date.max
    ) -> "DayRange | None":
        window = DayRange(max(self.start, first), min(self.end, last))
        return window if window else None

    def widened(self, days: int) -> "DayRange":
        return DayRange(self.start - days * DAY, self.end + days * DAY)

    def bounds(self) -> tuple[datetime, datetime]:
        return midnight(self.start), midnight(self.end) + DAY

    def chunks(self, size: int) -> Iterator["DayRange"]:
        cursor = self.start
        while cursor <= self.end:
            last = min(self.end, cursor + (size - 1) * DAY)
            yield DayRange(cursor, last)
            cursor = last + DAY

    def missing(self, covered: Container[date]) -> list["DayRange"]:
        ranges: list[DayRange] = []
        day = self.start
        while day <= self.end:
            if day in covered:
                day += DAY
                continue
            first = day
            while day <= self.end and day not in covered:
                day += DAY
            ranges.append(DayRange(first, day - DAY))
        return ranges

    def buckets(
        self, size: int, epoch: date, skip: Container[date] = ()
    ) -> list["DayRange"]:
        spans: set[tuple[date, date]] = set()
        for day in self:
            if day in skip:
                continue
            first = epoch + ((day - epoch).days // size) * size * DAY
            spans.add((first, first + (size - 1) * DAY))
        return [
            DayRange(max(first, self.start), min(last, self.end))
            for first, last in sorted(spans)
        ]
