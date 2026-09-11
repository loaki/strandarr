from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from strandarr.models.base import Base


class Stranding(Base):
    __tablename__ = "stranding"
    __table_args__ = (
        Index("ix_stranding_dedup", "source", "external_id", unique=True),
    )

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    species_scientific: Mapped[str | None] = mapped_column(String(255))
    species_common: Mapped[str | None] = mapped_column(String(255))
    individual_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    coordinate_uncertainty_m: Mapped[float | None] = mapped_column(Float)

    coordinate_precision_deg: Mapped[float | None] = mapped_column(Float)

    time_uncertainty_hours: Mapped[float | None] = mapped_column(Float)

    location_precision: Mapped[str | None] = mapped_column(String(32))
