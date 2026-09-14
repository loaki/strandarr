from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from strandarr.models.base import Base


class DriftRelease(Base):
    __tablename__ = "drift_release"
    __table_args__ = (
        Index("ix_drift_release_dedup", "release_at", "model_version", unique=True),
    )

    release_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    model_version: Mapped[str] = mapped_column(String(16), nullable=False)
    complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class DriftArrival(Base):
    __tablename__ = "drift_arrival"
    __table_args__ = (
        Index(
            "ix_drift_arrival_dedup",
            "release_at",
            "coastal_segment_id",
            "arrival_at",
            "model_version",
            unique=True,
        ),
        Index("ix_drift_arrival_arrival_at", "arrival_at"),
    )

    release_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    model_version: Mapped[str] = mapped_column(String(16), nullable=False)
    coastal_segment_id: Mapped[int] = mapped_column(
        ForeignKey("coastal_segment.id", ondelete="CASCADE"), nullable=False
    )
    arrival_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expected_count: Mapped[float] = mapped_column(Float, nullable=False)
