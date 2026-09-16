import logging
from dataclasses import dataclass
from datetime import date, timedelta

from strandarr import kinds
from strandarr.analysis import coast, drift
from strandarr.db.queries import coverage, environment, observation, reference
from strandarr.db.upsert import replace, upsert
from strandarr.jobs.task import Context, Payload, Task
from strandarr.models import DriftDaily, DriftRelease
from strandarr.timeframe import midnight

logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class DriftArrivals(Task):
    def run(self, ctx: Context, payload: Payload) -> int:
        days = payload.span
        done = ctx.covered(days, payload.force)
        effort = coverage.covered(ctx.session, kinds.VESSEL_POSITIONS, days)
        todo = [day for day in days if day not in done]
        pending = [day for day in todo if day in effort]
        if done:
            logger.info("drift: %d day(s) already complete, skipped", len(done))
        if len(pending) < len(todo):
            logger.info(
                "drift: %d day(s) waiting on %s, not simulated",
                len(todo) - len(pending),
                kinds.VESSEL_POSITIONS,
            )
        if not pending:
            return 0

        index = reference.segments(ctx.session)
        raster = coast.build(index, drift.BEACHING_DISTANCE_KM)
        return sum(self._day(ctx, day, raster) for day in pending)

    def _day(self, ctx: Context, day: date, raster: coast.Coast) -> int:
        released_at = midnight(day)
        seeds = drift.seeds(observation.vessel_day(ctx.session, day), day)
        forcing = drift.build_forcing(environment.forcing(ctx.session, day))
        result = drift.simulate(seeds, forcing, raster)

        totals: dict[tuple[date, int], float] = {}
        for arrival in result.arrivals:
            landed = (released_at + timedelta(hours=arrival.hour)).date()
            key = (landed, arrival.segment_id)
            totals[key] = totals.get(key, 0.0) + arrival.drift_index

        replace(
            ctx.session,
            DriftDaily,
            [
                DriftDaily(
                    release_day=day,
                    day=landed,
                    coastal_segment_id=segment_id,
                    model_version=drift.MODEL_VERSION,
                    drift_index=value,
                )
                for (landed, segment_id), value in totals.items()
            ],
            DriftDaily.release_day == day,
            DriftDaily.model_version == drift.MODEL_VERSION,
        )
        upsert(
            ctx.session,
            DriftRelease,
            [
                DriftRelease(
                    release_at=released_at,
                    model_version=drift.MODEL_VERSION,
                    complete=result.complete,
                )
            ],
            overwrite=True,
        )
        ctx.record({day: len(result.arrivals)}, complete=result.complete)
        ctx.commit()

        logger.info(
            "drift %s: %d seed(s), %d particle(s), %.3f released -> %.3f stranded "
            "on %d segment-hour(s), %dh of forcing%s",
            day,
            len(seeds),
            result.particles,
            result.released_weight,
            result.stranded_weight,
            len(result.arrivals),
            result.forcing_hours,
            "" if result.complete else " (incomplete, will re-run)",
        )
        return len(result.arrivals)
