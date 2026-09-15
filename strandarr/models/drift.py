from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String
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
