from datetime import date
from typing import ClassVar

from sqlalchemy import Date, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from strandarr.models.base import Base


class SegmentRisk(Base):
    __tablename__ = "segment_risk"
    __conflict__: ClassVar[tuple[str, ...]] = ("day", "coastal_segment_id")

    SIGNALS: ClassVar[tuple[str, ...]] = (
        "persistence",
        "drift_index",
        "swell_m",
        "wave_m",
        "onshore_m",
        "period_s",
    )

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    coastal_segment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("coastal_segment.id", ondelete="CASCADE"), primary_key=True
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
