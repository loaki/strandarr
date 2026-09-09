from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from strandarr.models.base import Base, CreationDate


class Stranding(Base, CreationDate):
    __tablename__ = "stranding"
    __table_args__ = (
        Index("ix_stranding_dedup", "source", "external_id", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    species_scientific: Mapped[str | None] = mapped_column(String(255), nullable=True)
    species_common: Mapped[str | None] = mapped_column(String(255), nullable=True)
    individual_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
