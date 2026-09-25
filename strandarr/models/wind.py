from datetime import datetime
from typing import ClassVar

from sqlalchemy import REAL, Boolean, DateTime, ForeignKey, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column

from strandarr.models.base import Base


class Wind(Base):
    __tablename__ = "wind"
    __conflict__: ClassVar[tuple[str, ...]] = ("valid_at", "cell_id")

    valid_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    cell_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("cell.id", ondelete="CASCADE"), primary_key=True
    )
    forecast: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    speed_kmh: Mapped[float] = mapped_column(REAL, nullable=False)
    direction_deg: Mapped[float] = mapped_column(REAL, nullable=False)
