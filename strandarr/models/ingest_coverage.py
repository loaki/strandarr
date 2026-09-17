from datetime import date, datetime
from typing import ClassVar

from sqlalchemy import Boolean, Date, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from strandarr.models.base import Base


class IngestCoverage(Base):
    __tablename__ = "ingest_coverage"
    __table_args__ = (Index("ix_ingest_coverage_dedup", "kind", "day", unique=True),)
    __conflict__: ClassVar[tuple[str, ...]] = (
        "kind",
        "day",
    )

    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cell_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
