from sqlalchemy import Float, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from strandarr.models.base import Base


class CoastalSegment(Base):
    __tablename__ = "coastal_segment"
    __table_args__ = (
        Index("ix_coastal_segment_external_id", "external_id", unique=True),
    )

    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    center_lat: Mapped[float] = mapped_column(Float, nullable=False)
    center_lon: Mapped[float] = mapped_column(Float, nullable=False)
    length_km: Mapped[float] = mapped_column(Float, nullable=False)
    coastline_orientation_deg: Mapped[float | None] = mapped_column(Float)
    path: Mapped[list[list[float]] | None] = mapped_column(JSONB)
