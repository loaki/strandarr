from datetime import datetime
from typing import ClassVar

from sqlalchemy import Boolean, DateTime, Float, Index
from sqlalchemy.orm import Mapped, mapped_column

from strandarr.models.base import Base


class Condition(Base):
    __tablename__ = "condition"
    __table_args__ = (
        Index("ix_condition_dedup", "valid_at", "lat", "lon", unique=True),
        Index("ix_condition_cell", "lat", "lon"),
    )
    __conflict__: ClassVar[tuple[str, ...]] = ("valid_at", "lat", "lon")

    MEASUREMENTS: ClassVar[tuple[str, ...]] = (
        "wind_speed_kmh",
        "wind_direction_deg",
        "current_speed_kmh",
        "current_direction_deg",
        "wave_height_m",
        "wave_direction_deg",
        "wave_period_s",
        "swell_height_m",
        "swell_direction_deg",
        "swell_period_s",
        "sea_surface_temperature_c",
        "sea_level_m",
    )

    valid_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    forecast: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    wind_speed_kmh: Mapped[float | None] = mapped_column(Float)
    wind_direction_deg: Mapped[float | None] = mapped_column(Float)
    current_speed_kmh: Mapped[float | None] = mapped_column(Float)
    current_direction_deg: Mapped[float | None] = mapped_column(Float)
    wave_height_m: Mapped[float | None] = mapped_column(Float)
    wave_direction_deg: Mapped[float | None] = mapped_column(Float)
    wave_period_s: Mapped[float | None] = mapped_column(Float)
    swell_height_m: Mapped[float | None] = mapped_column(Float)
    swell_direction_deg: Mapped[float | None] = mapped_column(Float)
    swell_period_s: Mapped[float | None] = mapped_column(Float)
    sea_surface_temperature_c: Mapped[float | None] = mapped_column(Float)
    sea_level_m: Mapped[float | None] = mapped_column(Float)
