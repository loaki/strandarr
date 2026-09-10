from datetime import datetime, timezone
from typing import Any, Generator

from sqlalchemy import DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.schema import MetaData


class BaseReprMixin:
    def _vars(self) -> Generator[tuple[str, Any], None, None]:
        for key, value in dict(sorted(vars(self).items())).items():
            if not key.startswith("_") and not isinstance(value, BaseReprMixin):
                yield key, value

    def __repr__(self) -> str:
        params = [f"{key}={value!r}" for key, value in self._vars()]
        return f"{self.__class__.__name__}({', '.join(params)})"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(BaseReprMixin, DeclarativeBase):
    metadata = MetaData()


class CreationDate:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class Observation(Base):
    """Something measured at a point in time.

    Abstract, so it builds no table of its own; it exists so the scheduler and the API can be
    handed a model class and still read `recorded_at` off it. Every read is a time-range scan,
    hence the index.
    """

    __abstract__ = True

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class SourcedObservation(Observation):
    """An observation that records which upstream provider it came from.

    Separate from Observation because gap detection is per-source for these -- two providers
    can cover the same day -- while wind and currents have only one provider each.
    """

    __abstract__ = True

    source: Mapped[str] = mapped_column(String(32), nullable=False)
