from typing import ClassVar

from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from strandarr.models.base import Base


class Vessel(Base):
    __tablename__ = "vessel"
    __table_args__ = (Index("ix_vessel_mmsi", "mmsi", unique=True),)
    __conflict__: ClassVar[tuple[str, ...]] = ("mmsi",)

    IDENTITY: ClassVar[tuple[str, ...]] = (
        "ship_name",
        "flag",
        "gear_type",
        "vessel_type",
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mmsi: Mapped[str] = mapped_column(String(16), nullable=False)
    ship_name: Mapped[str | None] = mapped_column(String(128))
    flag: Mapped[str | None] = mapped_column(String(8))
    gear_type: Mapped[str | None] = mapped_column(String(64))
    vessel_type: Mapped[str | None] = mapped_column(String(64))
