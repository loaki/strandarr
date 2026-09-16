from typing import ClassVar

from sqlalchemy import Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from strandarr.models.base import Base


class SegmentClimatology(Base):
    __tablename__ = "segment_climatology"
    __table_args__ = (
        Index(
            "ix_segment_climatology_dedup",
            "coastal_segment_id",
            "day_of_year",
            "model_version",
            unique=True,
        ),
        Index("ix_segment_climatology_day", "day_of_year"),
    )
    __conflict__: ClassVar[tuple[str, ...]] = (
        "coastal_segment_id",
        "day_of_year",
        "model_version",
    )

    coastal_segment_id: Mapped[int] = mapped_column(
        ForeignKey("coastal_segment.id", ondelete="CASCADE"), nullable=False
    )
    day_of_year: Mapped[int] = mapped_column(Integer, nullable=False)
    model_version: Mapped[str] = mapped_column(String(16), nullable=False)
    observed: Mapped[float] = mapped_column(Float, nullable=False)
    expected_per_day: Mapped[float] = mapped_column(Float, nullable=False)
    probability: Mapped[float] = mapped_column(Float, nullable=False)
    years: Mapped[int] = mapped_column(Integer, nullable=False)
