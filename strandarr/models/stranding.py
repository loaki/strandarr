from datetime import datetime
from typing import ClassVar

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from strandarr.models.base import Base


class Stranding(Base):
    __tablename__ = "stranding"
    __table_args__ = (
        Index("ix_stranding_dedup", "source", "external_id", unique=True),
    )
    __conflict__: ClassVar[tuple[str, ...]] = ("source", "external_id")

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
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
