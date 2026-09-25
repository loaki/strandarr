from typing import ClassVar

from sqlalchemy import Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from strandarr.models.base import Base


class CoastalSegment(Base):
    __tablename__ = "coastal_segment"
    __table_args__ = (
        Index("ix_coastal_segment_external_id", "external_id", unique=True),
    )
    __conflict__: ClassVar[tuple[str, ...]] = ("external_id",)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    center_lat: Mapped[float] = mapped_column(Float, nullable=False)
    center_lon: Mapped[float] = mapped_column(Float, nullable=False)
    length_km: Mapped[float] = mapped_column(Float, nullable=False)
    orientation_deg: Mapped[float | None] = mapped_column(Float)
    path: Mapped[list[list[float]] | None] = mapped_column(JSONB)
