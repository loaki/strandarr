import logging
from dataclasses import dataclass

from strandarr import sources
from strandarr.connectors import gfw
from strandarr.db.upsert import upsert
from strandarr.grid import GRID
from strandarr.jobs.task import Context, Payload, Task
from strandarr.models import VesselPosition

logger = logging.getLogger(__name__)


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
