from datetime import datetime
from typing import ClassVar

from sqlalchemy import REAL, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from strandarr.models.base import Base


class VesselPosition(Base):
    __tablename__ = "vessel_position"
    __conflict__: ClassVar[tuple[str, ...]] = ("recorded_at", "vessel_id")

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    vessel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vessel.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    lat: Mapped[float] = mapped_column(REAL, nullable=False)
    lon: Mapped[float] = mapped_column(REAL, nullable=False)
    effort_hours: Mapped[float | None] = mapped_column(REAL)
