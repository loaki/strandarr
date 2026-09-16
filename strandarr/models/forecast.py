from datetime import date
from typing import ClassVar

from sqlalchemy import Date, Float, ForeignKey, Index, String
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
            "model_version",
            unique=True,
        ),
        Index("ix_drift_daily_day", "day"),
    )
    __conflict__: ClassVar[tuple[str, ...]] = (
        "release_day",
        "day",
        "coastal_segment_id",
        "model_version",
    )

    release_day: Mapped[date] = mapped_column(Date, nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    coastal_segment_id: Mapped[int] = mapped_column(
        ForeignKey("coastal_segment.id", ondelete="CASCADE"), nullable=False
    )
    model_version: Mapped[str] = mapped_column(String(16), nullable=False)
    drift_index: Mapped[float] = mapped_column(Float, nullable=False)


class SegmentForecast(Base):
    __tablename__ = "segment_forecast"
    __table_args__ = (
        Index(
            "ix_segment_forecast_dedup",
            "day",
            "coastal_segment_id",
            "model_version",
            unique=True,
        ),
        Index("ix_segment_forecast_day", "day"),
    )
    __conflict__: ClassVar[tuple[str, ...]] = (
        "day",
        "coastal_segment_id",
        "model_version",
    )

    day: Mapped[date] = mapped_column(Date, nullable=False)
    coastal_segment_id: Mapped[int] = mapped_column(
        ForeignKey("coastal_segment.id", ondelete="CASCADE"), nullable=False
    )
    model_version: Mapped[str] = mapped_column(String(16), nullable=False)
    probability: Mapped[float] = mapped_column(Float, nullable=False)
    persistence: Mapped[float] = mapped_column(Float, nullable=False)
    drift_index: Mapped[float] = mapped_column(Float, nullable=False)
    swell_m: Mapped[float] = mapped_column(Float, nullable=False)
    onshore_m: Mapped[float] = mapped_column(Float, nullable=False)
