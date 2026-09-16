import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from strandarr.analysis.geo import GRID, BBox, Point
from strandarr.analysis.timeframe import DayRange
from strandarr.config import settings
from strandarr.connectors import gfw, open_meteo, sources
from strandarr.db.queries import reference
from strandarr.db.upsert import upsert
from strandarr.jobs.task import Context, Payload, Task
from strandarr.models import MarineCondition, Stranding, VesselPosition

logger = logging.getLogger(__name__)

Fetch = Callable[[BBox, DayRange], list[Stranding]]


@dataclass(frozen=True, kw_only=True)
class ArchiveIngest(Task):
    product: open_meteo.Product

    def run(self, ctx: Context, payload: Payload) -> int:
        days = payload.span
        covered = ctx.covered(days, payload.force)
        ranges = days.missing(covered)
        if not ranges:
            logger.info(
                "%s: %s..%s is already covered",
                self.product.source,
                days.start,
                days.end,
            )
            return 0

        points = reference.ingest_points(ctx.session)
        logger.info(
            "%s: requesting %d of %d grid cells, %d day(s) already covered",
            self.product.source,
            len(points),
            len(GRID.points()),
            len(covered),
        )
        return sum(
            self._span(ctx, points, chunk, payload.force)
            for span in ranges
            for chunk in span.chunks(open_meteo.DAYS_PER_REQUEST)
        )

    def _span(
        self, ctx: Context, points: list[Point], days: DayRange, force: bool
    ) -> int:
        written = 0
        counts: Counter[date] = Counter()
        for rows in open_meteo.iter_archive(self.product, points, days):
            written += upsert(ctx.session, MarineCondition, rows, overwrite=force)
            counts.update(row.valid_at.date() for row in rows)
            ctx.commit()
        ctx.record({day: count for day, count in counts.items() if day in days})
        ctx.commit()
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
        days = payload.span
        covered = ctx.covered(days, payload.force)
        if covered:
            logger.info(
                "%s: %d day(s) already covered, not re-requested",
                sources.GFW_FISHING,
                len(covered),
            )

        total = 0
        for day, positions in gfw.iter_positions(
            gfw.FISHING_DATASET, sources.GFW_FISHING, GRID.bbox, days, covered
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
        rows = self.fetch(GRID.bbox, payload.span)
        written = upsert(ctx.session, Stranding, rows, overwrite=payload.force)
        ctx.commit()
        return written
