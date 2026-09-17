import logging
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from sqlalchemy.orm import Session

from strandarr.analysis import Float, climatology, risk
from strandarr.analysis.coast import SegmentIndex
from strandarr.analysis.timeframe import DayRange
from strandarr.db.queries import coverage, environment, prediction, reference
from strandarr.db.upsert import replace
from strandarr.jobs.task import Context, Payload, Task
from strandarr.models import SegmentRisk
from strandarr.services import observations

logger = logging.getLogger(__name__)


def sea_state(session: Session, index: SegmentIndex, day: date) -> dict[str, Float]:
    rows = environment.sea_state(session, day)
    if not rows:
        return {}
    kernel = risk.weights(
        index,
        np.array([row[0] for row in rows], dtype=np.float64),
        np.array([row[1] for row in rows], dtype=np.float64),
    )

    def resolve(column: int) -> Float:
        values = np.array([float(row[column] or 0.0) for row in rows], dtype=np.float64)
        return np.asarray(kernel @ values)

    return {
        "swell": resolve(2),
        "wave": resolve(3),
        "period": resolve(4),
        "onshore": risk.onshore(index, resolve(5), resolve(6)),
    }


@dataclass(frozen=True, kw_only=True)
class RiskZones(Task):
    def run(self, ctx: Context, payload: Payload) -> int:
        span = self.days(payload)
        if span is None:
            return 0
        settled = ctx.settled(span, payload.force)
        days = [day for day in span if day not in settled]
        if not days:
            return 0

        final = self._final(ctx.session, DayRange(days[0], days[-1]))
        live = coverage.provisional_from(ctx.session)
        index = reference.segments(ctx.session)
        observed, _ = observations.load(ctx.session, index)
        pairs = observations.pairs(observed)
        recent = risk.persistence(pairs)
        seasons = climatology.build(pairs, len(index))
        baseline = risk.reference(seasons, len(index))

        total = 0
        for day in days:
            signals = sea_state(ctx.session, index, day)
            complete = bool(signals) and day in final
            signals["persistence"] = recent.score(day, len(index))
            signals["drift"] = index.vector(prediction.drift_recent(ctx.session, day))
            seasonal = risk.seasonal(seasons, day, len(index))
            offset = risk.logit(seasonal) - baseline
            probability = risk.predict(signals, offset, len(index))
            blank = index.blank()
            source = risk.OBSERVED if complete else risk.FORECAST

            total += replace(
                ctx.session,
                SegmentRisk,
                [
                    SegmentRisk(
                        day=day,
                        coastal_segment_id=segment_id,
                        model_version=risk.MODEL_VERSION,
                        source=source,
                        probability=float(probability[position]),
                        seasonal=float(seasonal[position]),
                        drift_index=float(signals["drift"][position]),
                        persistence=float(signals["persistence"][position]),
                        swell_m=float(signals.get("swell", blank)[position]),
                        onshore_m=float(signals.get("onshore", blank)[position]),
                    )
                    for position, segment_id in enumerate(index.ids)
                ],
                SegmentRisk.day == day,
                SegmentRisk.model_version == risk.MODEL_VERSION,
            )
            ctx.record(
                {day: len(index)},
                complete=complete,
                recheck=not complete and day < live,
            )
            ctx.commit()

        logger.info("risk: %d segment-day(s) for %s..%s", total, days[0], days[-1])
        return total

    def _final(self, session: Session, days: DayRange) -> set[date]:
        seen = timedelta(days=risk.LAG_DAYS)
        archive = coverage.archived(
            session, DayRange(days.start - seen, days.end - seen)
        )
        landed = DayRange(
            days.start - timedelta(days=prediction.DRIFT_WINDOW_DAYS), days.end
        )
        simulated = coverage.simulated_days(session, landed)
        return {
            day
            for day in days
            if day - seen in archive
            and all(
                day - timedelta(days=offset) in simulated
                for offset in range(prediction.DRIFT_WINDOW_DAYS + 1)
            )
        }
