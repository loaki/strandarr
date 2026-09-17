from datetime import date
from typing import ClassVar

from sqlalchemy import Date, Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from strandarr.models.base import Base


class SegmentRisk(Base):
    __tablename__ = "segment_risk"
    __table_args__ = (
        Index("ix_segment_risk_dedup", "day", "coastal_segment_id", unique=True),
        Index("ix_segment_risk_day", "day"),
    )
    __conflict__: ClassVar[tuple[str, ...]] = ("day", "coastal_segment_id")

    SIGNALS: ClassVar[tuple[str, ...]] = (
        "persistence",
        "drift_index",
        "swell_m",
        "wave_m",
        "onshore_m",
        "period_s",
    )

    day: Mapped[date] = mapped_column(Date, nullable=False)
    coastal_segment_id: Mapped[int] = mapped_column(
        ForeignKey("coastal_segment.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    probability: Mapped[float] = mapped_column(Float, nullable=False)
    seasonal: Mapped[float] = mapped_column(Float, nullable=False)

    persistence: Mapped[float] = mapped_column(Float, nullable=False)
    drift_index: Mapped[float] = mapped_column(Float, nullable=False)
    swell_m: Mapped[float] = mapped_column(Float, nullable=False)
    wave_m: Mapped[float] = mapped_column(Float, nullable=False)
    onshore_m: Mapped[float] = mapped_column(Float, nullable=False)
    period_s: Mapped[float] = mapped_column(Float, nullable=False)
