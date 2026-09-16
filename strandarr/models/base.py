from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.schema import MetaData


def utc_now() -> datetime:
    return datetime.now(UTC)


class IDMixin:
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class Base(IDMixin, TimestampMixin, DeclarativeBase):
    metadata = MetaData()

    __conflict__: ClassVar[tuple[str, ...]] = ()

    def __repr__(self) -> str:
        loaded = sorted(
            (key, value) for key, value in vars(self).items() if not key.startswith("_")
        )
        params = ", ".join(f"{key}={value!r}" for key, value in loaded)
        return f"{type(self).__name__}({params})"
