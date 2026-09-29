from typing import ClassVar

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from strandarr.models.base import Base


class GfwVessel(Base):
    __tablename__ = "gfw_vessel"
    __table_args__ = (Index("ix_gfw_vessel_vessel_id", "vessel_id"),)
    __conflict__: ClassVar[tuple[str, ...]] = ("gfw_id",)

    gfw_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    vessel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vessel.id", ondelete="CASCADE"), nullable=False
    )
