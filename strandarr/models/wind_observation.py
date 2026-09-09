from datetime import datetime

from sqlalchemy import DateTime, Float, Index
from sqlalchemy.orm import Mapped, mapped_column

from strandarr.models.base import Base, CreationDate


class WindObservation(Base, CreationDate):
    __tablename__ = "wind_observation"
    __table_args__ = (
        Index("ix_wind_observation_dedup", "recorded_at", "lat", "lon", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    speed: Mapped[float] = mapped_column(Float, nullable=False)
    direction: Mapped[float] = mapped_column(Float, nullable=False)
