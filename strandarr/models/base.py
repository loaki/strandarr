from datetime import datetime, timezone
from typing import Any, Generator

from sqlalchemy import DateTime
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
