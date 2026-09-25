from typing import ClassVar

from sqlalchemy import REAL, Index, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column

from strandarr.models.base import Base


class Cell(Base):
    __tablename__ = "cell"
    __table_args__ = (Index("ix_cell_position", "lat", "lon", unique=True),)
    __conflict__: ClassVar[tuple[str, ...]] = ("lat", "lon")

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    lat: Mapped[float] = mapped_column(REAL, nullable=False)
    lon: Mapped[float] = mapped_column(REAL, nullable=False)
