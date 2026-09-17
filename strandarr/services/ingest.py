import logging
from collections.abc import Callable
from dataclasses import dataclass

from strandarr.analysis.geo import GRID, BBox, Point
from strandarr.analysis.timeframe import DayRange
from strandarr.config import settings
from strandarr.connectors import gfw, open_meteo, sources
from strandarr.db.queries import environment, reference
from strandarr.db.upsert import upsert
from strandarr.jobs.task import Context, Payload, Task
from strandarr.models import MarineCondition, Stranding, VesselPosition

logger = logging.getLogger(__name__)

Fetch = Callable[[BBox, DayRange], list[Stranding]]


@dataclass(frozen=True, kw_only=True)
class ArchiveIngest(Task):
    product: open_meteo.Product

    def run(self, ctx: Context, payload: Payload) -> int:
        days = self.days(payload)
        if days is None:
            return 0
        stored = ctx.settled(days, payload.force)
        ranges = days.missing(stored)
        if not ranges:
            logger.info(
                "%s: %s..%s is already settled",
                self.product.source,
                days.start,
                days.end,
            )
            return 0

        points = reference.ingest_points(ctx.session)
        logger.info(
            "%s: requesting %d of %d grid cells, %d day(s) already settled",
            self.product.source,
            len(points),
            len(GRID.points()),
            len(stored),
        )
        return sum(
            self._span(ctx, points, chunk)
            for span in ranges
            for chunk in span.chunks(open_meteo.DAYS_PER_REQUEST)
        )

    def _span(self, ctx: Context, points: list[Point], days: DayRange) -> int:
        written = 0
        for rows in open_meteo.iter_archive(self.product, points, days):
            written += upsert(ctx.session, MarineCondition, rows, overwrite=True)
            ctx.commit()
        counts = environment.day_counts(ctx.session, self.product.source, days)
        moved = ctx.measure({day: counts.get(day, (0, 0)) for day in days})
        ctx.commit()
        logger.info(
            "%s: %s..%s stored, %d day(s) changed state",
            self.product.source,
            days.start,
            days.end,
            moved,
        )
        return written


@dataclass(frozen=True, kw_only=True)
class ForecastIngest(Task):
    def run(self, ctx: Context, payload: Payload) -> int:
        points = reference.ingest_points(ctx.session)
        total = 0
        for product in open_meteo.FORECASTS:
            for rows in open_meteo.iter_forecast(
                product, points, settings.marine_forecast_hours
            ):
                total += upsert(ctx.session, MarineCondition, rows, overwrite=True)
                ctx.commit()
        return total


@dataclass(frozen=True, kw_only=True)
class VesselIngest(Task):
    def run(self, ctx: Context, payload: Payload) -> int:
        days = self.days(payload)
        if days is None:
            return 0
        stored = ctx.settled(days, payload.force)
        if stored:
            logger.info(
                "%s: %d day(s) already settled, not re-requested",
                sources.GFW_FISHING,
                len(stored),
            )

        total = 0
        for day, positions in gfw.iter_positions(
            gfw.FISHING_DATASET, sources.GFW_FISHING, GRID.bbox, days, stored
        ):
            written = upsert(ctx.session, VesselPosition, positions, overwrite=True)
            ctx.record({day: written})
            ctx.commit()
            total += written
        return total


@dataclass(frozen=True, kw_only=True)
class StrandingIngest(Task):
    fetch: Fetch

    def run(self, ctx: Context, payload: Payload) -> int:
        days = self.days(payload)
        if days is None:
            return 0
        rows = self.fetch(GRID.bbox, days)
        written = upsert(ctx.session, Stranding, rows, overwrite=payload.force)
        ctx.commit()
        return written
