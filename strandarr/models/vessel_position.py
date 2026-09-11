from datetime import datetime

from sqlalchemy import DateTime, Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from strandarr.models.base import Base


class VesselPosition(Base):
    __tablename__ = "vessel_position"
    __table_args__ = (
        Index("ix_vessel_position_dedup", "mmsi", "recorded_at", unique=True),
    )

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    mmsi: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    ship_name: Mapped[str | None] = mapped_column(String(128))
    flag: Mapped[str | None] = mapped_column(String(8))
    gear_type: Mapped[str | None] = mapped_column(String(64))
    vessel_type: Mapped[str | None] = mapped_column(String(64))
    effort_hours: Mapped[float | None] = mapped_column(Float)
