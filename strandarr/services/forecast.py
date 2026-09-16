import logging
from dataclasses import dataclass
from datetime import date

import numpy as np

from strandarr.analysis import Float, conditions, forecast, persistence
from strandarr.db.queries import environment, prediction, reference
from strandarr.db.upsert import replace
from strandarr.jobs.task import Context, Payload, Task
from strandarr.models import SegmentForecast
from strandarr.segments import SegmentIndex
from strandarr.services import observations

logger = logging.getLogger(__name__)


def sea_state(ctx: Context, index: SegmentIndex, day: date) -> dict[str, Float]:
    rows = environment.sea_state(ctx.session, day)
    if not rows:
        return {}
    weights = conditions.weights(
        index,
        np.array([row[0] for row in rows], dtype=np.float64),
        np.array([row[1] for row in rows], dtype=np.float64),
    )

    def resolve(column: int) -> Float:
        values = np.array([float(row[column] or 0.0) for row in rows], dtype=np.float64)
        return np.asarray(weights @ values)

    return {
        "swell": resolve(2),
        "wave": resolve(3),
        "period": resolve(4),
        "onshore": conditions.onshore(index, resolve(5), resolve(6)),
    }


@dataclass(frozen=True, kw_only=True)
class ForecastZones(Task):
    def run(self, ctx: Context, payload: Payload) -> int:
        days = payload.span
        index = reference.segments(ctx.session)
        observed, _ = observations.load(ctx.session, index)
        recent = persistence.build(observations.pairs(observed))

        total = 0
        for day in days:
            signals = sea_state(ctx, index, day)
            complete = bool(signals)
            signals["persistence"] = recent.score(day, len(index))
            signals["drift"] = index.vector(prediction.drift_recent(ctx.session, day))
            probability = forecast.predict(signals, len(index))
            blank = index.blank()

            total += replace(
                ctx.session,
                SegmentForecast,
                [
                    SegmentForecast(
                        day=day,
                        coastal_segment_id=segment_id,
                        model_version=forecast.MODEL_VERSION,
                        probability=float(probability[position]),
                        persistence=float(signals["persistence"][position]),
                        drift_index=float(signals["drift"][position]),
                        swell_m=float(signals.get("swell", blank)[position]),
                        onshore_m=float(signals.get("onshore", blank)[position]),
                    )
                    for position, segment_id in enumerate(index.ids)
                ],
                SegmentForecast.day == day,
                SegmentForecast.model_version == forecast.MODEL_VERSION,
            )
            ctx.record({day: len(index)}, complete=complete)
            ctx.commit()

        logger.info(
            "forecast: %d segment-day(s) for %s..%s", total, days.start, days.end
        )
        return total
