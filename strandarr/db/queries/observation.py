from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from strandarr import sources
from strandarr.models import MarineCondition, Stranding, VesselPosition
from strandarr.timeframe import DayRange

Window = tuple[datetime, datetime]

TIMELINES = (MarineCondition.valid_at, VesselPosition.recorded_at)

FLEET_FIELDS: Sequence[tuple[str, Any]] = (
    ("flag", VesselPosition.flag),
    ("gear_type", VesselPosition.gear_type),
    ("vessel_type", VesselPosition.vessel_type),
    ("name", VesselPosition.ship_name),
)


def vessels(session: Session, window: Window) -> list[VesselPosition]:
    start, end = window
    return list(
        session.execute(
            select(VesselPosition).where(
                VesselPosition.recorded_at >= start, VesselPosition.recorded_at < end
            )
        ).scalars()
    )


def vessel_day(session: Session, day: date) -> list[VesselPosition]:
    return vessels(session, DayRange(day, day).bounds())


def strandings(session: Session, window: Window) -> list[Stranding]:
    start, end = window
    return list(
        session.execute(
            select(Stranding).where(
                Stranding.recorded_at >= start, Stranding.recorded_at < end
            )
        ).scalars()
    )


def stranding_points(session: Session) -> Sequence[Any]:
    return session.execute(
        select(
            Stranding.recorded_at,
            Stranding.lat,
            Stranding.lon,
            Stranding.individual_count,
            Stranding.location_precision,
        )
    ).all()


def observed_range(session: Session) -> dict[str, datetime | None]:
    bounds = [
        session.execute(select(func.min(column), func.max(column))).one()
        for column in TIMELINES
    ]
    lows = [low for low, _ in bounds if low is not None]
    highs = [high for _, high in bounds if high is not None]
    return {"min": min(lows) if lows else None, "max": max(highs) if highs else None}


def _last_known(
    session: Session, column: InstrumentedAttribute[str | None]
) -> dict[str, str]:
    stmt = (
        select(VesselPosition.mmsi, column)
        .where(VesselPosition.source == sources.GFW_FISHING, column.isnot(None))
        .distinct(VesselPosition.mmsi)
        .order_by(VesselPosition.mmsi, VesselPosition.recorded_at.desc())
    )
    return {
        mmsi: value for mmsi, value in session.execute(stmt).all() if value is not None
    }


def fishing_fleet(session: Session) -> dict[str, dict[str, str]]:
    fleet: dict[str, dict[str, str]] = {
        mmsi: {}
        for mmsi in session.execute(
            select(VesselPosition.mmsi)
            .where(VesselPosition.source == sources.GFW_FISHING)
            .distinct()
        ).scalars()
    }
    for field, column in FLEET_FIELDS:
        for mmsi, value in _last_known(session, column).items():
            if mmsi in fleet:
                fleet[mmsi][field] = value
    return fleet
