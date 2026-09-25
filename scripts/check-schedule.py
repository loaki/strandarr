#!/usr/bin/env python
"""Prove the scheduler eventually covers every day it claims to.

A chunked job is enqueued as soon as any of its days is inside the horizon, and
it ingests only the days available when it runs. If it is never re-opened, the
trailing chunk stays partial for ever -- the job says `done` while the
data is not there, which is invisible from the job table.

Run it from `make all-checks`. It needs no database.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strandarr import jobs
from strandarr.timeframe import DayRange

DAY = timedelta(days=1)
FIRST_DAY = date(2026, 1, 15)
CALENDAR_DAYS = 140
FORECAST_DAYS = 3


def covered(spec: jobs.Spec) -> tuple[set[date], set[date]]:
    """Replay `schedule` then `work`, once a day, and report wanted vs ingested."""
    queue: dict[date, str] = {}
    ingested: set[date] = set()
    for offset in range(CALENDAR_DAYS):
        today = FIRST_DAY + offset * DAY
        span = DayRange(
            today - jobs.DEFAULT_BACKFILL_DAYS * DAY, today + FORECAST_DAYS * DAY
        )
        for bucket in spec.buckets(today, span):
            queue.setdefault(bucket, "pending")  # ON CONFLICT DO NOTHING
        if spec.volatile:
            since = today - spec.volatile * DAY
            for bucket, state in queue.items():
                if bucket >= since and state == "done":
                    queue[bucket] = "pending"
        for bucket in sorted(queue):
            if queue[bucket] != "pending":
                continue
            days = spec.days(today, bucket)
            if days is not None:
                ingested.update(days)
            queue[bucket] = "done"

    last = FIRST_DAY + (CALENDAR_DAYS - 1) * DAY - timedelta(days=spec.lag)
    wanted = {
        day
        for day in DayRange(FIRST_DAY - jobs.DEFAULT_BACKFILL_DAYS * DAY, last)
        if spec.first <= day <= spec.last
    }
    return wanted, ingested


def main() -> int:
    failed = False
    for kind, spec in jobs.TASKS.items():
        if spec.rolling:
            print(f"  --  {kind:<20} rolling")
            continue
        wanted, ingested = covered(spec)
        missing = sorted(wanted - ingested)
        share = 100 * (len(wanted) - len(missing)) / len(wanted) if wanted else 100.0
        print(
            f"  {'GAP' if missing else 'ok '} {kind:<20}"
            f" {len(wanted) - len(missing):>4}/{len(wanted):<4} day(s) {share:5.1f}%"
            + (f"  first gap {missing[0]}" if missing else "")
        )
        failed |= bool(missing)

    if failed:
        print(
            "\nA kind never covers some of its days. A chunked kind needs "
            "volatile >= chunk + lag so its trailing chunk is re-opened.",
            file=sys.stderr,
        )
        return 1
    print("\nevery kind reaches full coverage")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
