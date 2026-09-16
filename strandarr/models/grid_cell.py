from typing import ClassVar

from sqlalchemy import Float, Index
from sqlalchemy.orm import Mapped, mapped_column

from strandarr.models.base import Base


class GridCell(Base):
    __tablename__ = "grid_cell"
    __table_args__ = (Index("ix_grid_cell_dedup", "lat", "lon", unique=True),)
    __conflict__: ClassVar[tuple[str, ...]] = (
        "lat",
        "lon",
    )

    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    elevation_m: Mapped[float] = mapped_column(Float, nullable=False)

    distance_to_coast_km: Mapped[float | None] = mapped_column(Float)
