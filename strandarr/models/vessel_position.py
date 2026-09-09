from datetime import datetime

from sqlalchemy import DateTime, Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from strandarr.models.base import Base, CreationDate


class VesselPosition(Base, CreationDate):
    __tablename__ = "vessel_position"
    __table_args__ = (
        Index("ix_vessel_position_dedup", "mmsi", "recorded_at", "source", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    mmsi: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    ship_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    flag: Mapped[str | None] = mapped_column(String(8), nullable=True)
    gear_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vessel_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effort_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
