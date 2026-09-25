from datetime import datetime
from typing import ClassVar

from sqlalchemy import REAL, Boolean, DateTime, ForeignKey, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column

from strandarr.models.base import Base


class Sea(Base):
    __tablename__ = "sea"
    __conflict__: ClassVar[tuple[str, ...]] = ("valid_at", "cell_id")

    MEASUREMENTS: ClassVar[tuple[str, ...]] = (
        "current_speed_kmh",
        "current_direction_deg",
        "wave_height_m",
        "wave_direction_deg",
        "wave_period_s",
        "swell_height_m",
        "swell_direction_deg",
        "swell_period_s",
    )

    valid_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    cell_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("cell.id", ondelete="CASCADE"), primary_key=True
    )
    forecast: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    current_speed_kmh: Mapped[float | None] = mapped_column(REAL)
    current_direction_deg: Mapped[float | None] = mapped_column(REAL)
    wave_height_m: Mapped[float | None] = mapped_column(REAL)
    wave_direction_deg: Mapped[float | None] = mapped_column(REAL)
    wave_period_s: Mapped[float | None] = mapped_column(REAL)
    swell_height_m: Mapped[float | None] = mapped_column(REAL)
    swell_direction_deg: Mapped[float | None] = mapped_column(REAL)
    swell_period_s: Mapped[float | None] = mapped_column(REAL)
