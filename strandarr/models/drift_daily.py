from datetime import date
from typing import ClassVar

from sqlalchemy import Date, Float, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from strandarr.models.base import Base


class DriftDaily(Base):
    __tablename__ = "drift_daily"
    __table_args__ = (
        Index(
            "ix_drift_daily_dedup",
            "release_day",
            "day",
            "coastal_segment_id",
            unique=True,
        ),
        Index("ix_drift_daily_day", "day"),
    )
    __conflict__: ClassVar[tuple[str, ...]] = (
        "release_day",
        "day",
        "coastal_segment_id",
    )

    release_day: Mapped[date] = mapped_column(Date, nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    coastal_segment_id: Mapped[int] = mapped_column(
        ForeignKey("coastal_segment.id", ondelete="CASCADE"), nullable=False
    )
    drift_index: Mapped[float] = mapped_column(Float, nullable=False)
