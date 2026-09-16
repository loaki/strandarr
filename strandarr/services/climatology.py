import logging
from dataclasses import dataclass

import numpy as np

from strandarr.analysis import climatology
from strandarr.db.queries import reference
from strandarr.db.upsert import replace
from strandarr.jobs.task import Context, Payload, Task
from strandarr.models import SegmentClimatology
from strandarr.services import observations

logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class ClimatologyBuild(Task):
    def run(self, ctx: Context, payload: Payload) -> int:
        index = reference.segments(ctx.session)
        observed, unmatched = observations.load(ctx.session, index)
        model = climatology.build(observations.pairs(observed), len(index))
        logger.info(
            "climatology: %d stranding(s) over %d year(s), %d beyond %.0f km, dropped",
            len(observed),
            len(model.years),
            unmatched,
            observations.SNAP_RADIUS_KM,
        )

        rate, chance = model.rate, model.chance
        written = replace(
            ctx.session,
            SegmentClimatology,
            [
                SegmentClimatology(
                    coastal_segment_id=index.ids[position],
                    day_of_year=int(slot) + 1,
                    model_version=climatology.MODEL_VERSION,
                    observed=float(model.total[position, slot]),
                    expected_per_day=float(rate[position, slot]),
                    probability=float(chance[position, slot]),
                    years=len(model.years),
                )
                for position, slot in zip(*np.nonzero(model.total), strict=True)
            ],
            SegmentClimatology.model_version == climatology.MODEL_VERSION,
        )
        ctx.commit()
        logger.info("climatology: %d segment-day(s) stored", written)
        return written
