from collections.abc import Callable
from dataclasses import dataclass

from strandarr.db.upsert import upsert
from strandarr.geo import BBox
from strandarr.grid import GRID
from strandarr.jobs.task import Context, Payload, Task
from strandarr.models import Stranding
from strandarr.timeframe import DayRange

Fetch = Callable[[BBox, DayRange], list[Stranding]]


@dataclass(frozen=True, kw_only=True)
class StrandingIngest(Task):
    fetch: Fetch

    def run(self, ctx: Context, payload: Payload) -> int:
        rows = self.fetch(GRID.bbox, payload.span)
        written = upsert(ctx.session, Stranding, rows, overwrite=payload.force)
        ctx.commit()
        return written
