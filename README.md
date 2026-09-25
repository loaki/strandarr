# strandarr

Cetacean stranding risk along the French Atlantic and Channel coast, from
fishing effort, ocean drift and the stranding record itself.

A cron enqueues work, a worker consumes it, a map shows the result.

## How it fits together

```
cron ──> strandarr schedule ──> job table ──> strandarr work ──> Postgres ──> map
```

Everything is a **job**, and a job is *one kind of work for one day*.
`(kind, day)` is unique, so the scheduler can offer every day it wants covered
and Postgres keeps only the ones not already queued. A `done` row means that
day is done; nothing counts rows to work that out.

Days whose inputs are still settling are re-opened by the scheduler: each kind
declares a `volatile` window (`strandarr/jobs.py`). Drift re-runs for 20 days
because that is how long its forcing window reaches into the forecast. It is
deliberately blunt — it can recompute more than needed, never less.

A chunked kind needs `volatile >= chunk + lag` for a second reason. Its job is
enqueued as soon as *any* of its days is old enough, and it ingests only the days
available when it runs, so without re-opening the trailing chunk would stay
partly ingested for ever while the job reads `done`.
`make check-schedule` replays the calendar and fails if any kind leaves a gap.

| kind | what it does | window |
|---|---|---|
| `gfw` | fishing effort per vessel-hour | from 2012, 4-day lag |
| `cmems` | currents, Stokes drift, waves and wind from Copernicus Marine | from 2022, 10-day chunks, 10 days ahead |
| `forecast` | wind forecast from Open-Meteo | rolling, re-run daily |
| `strandings_gbif` | Pelagis archive via GBIF | up to 2022, yearly chunks |
| `strandings_pelagis` | Pelagis histo-carto | from 2023, 30-day chunks |
| `drift` | OpenDrift simulation per release day | from 2022, 4-day lag |
| `risk` | probability per segment per day | from 2022, forecast days ahead |

`strandarr status` shows what is queued, what failed, and what is waiting on
inputs. A job whose inputs never arrive is deferred, not retried, and is failed
after `MAX_DEFERRALS` so it cannot re-queue itself for ever.

## Setup

```bash
cp .env.example .env          # then fill in GFW_API_TOKEN, AISSTREAM_API_KEY and CMEMS_*
docker compose up -d          # db, init (migrate + coastline), web, worker, aisstream
```

`init` runs `alembic upgrade head` then `strandarr reference`, which fetches the
Natural Earth coastline, keeps the French Atlantic and Channel coast, cuts it
into ~10 km segments, and stores the 0.25° cells of `grid_points.json`. Every
prediction is made for one of those segments, so this has to happen before
anything else.

## Running it

Ingest is driven from the CLI — there is no scheduler service. Point cron at it:

```cron
0 */6 * * * cd /path/to/strandarr && docker compose run --rm --no-deps worker strandarr schedule >> /var/log/strandarr.log 2>&1
```

`schedule` is idempotent. Running it twice in a minute does nothing the second
time; a missed run is caught by the next one. By default it covers the last 14
days plus the forecast horizon.

```bash
strandarr schedule                       # the rolling window (what cron runs)
strandarr schedule --start 2022-01-01    # backfill history, once
strandarr status                         # what the queue holds
strandarr retry --kind gfw               # re-queue failed jobs after fixing a token
strandarr work                           # consume the queue (the worker service)
```

`schedule` skips days already `done`. To compute a day *again* — after a refit,
or after changing the model — re-open it explicitly:

```bash
strandarr schedule --start 2022-01-01 --kind risk --redo
```

Jobs are claimed oldest day first, because drift for a day feeds the risk of the
days after it. A long backfill therefore runs ahead of the present: queue it in
stages (a year at a time) and let each drain, or the live map waits behind it.

Several workers can share the queue (`docker compose up -d --scale worker=6`).
A job left `running` by a worker that died is released after two hours.

### Backfill cost

From 2022 to today, measured per unit on one worker:

| kind | per unit | total |
|---|---|---|
| `cmems` | ~6 min per 10-day chunk | ~17 h |
| `gfw` | ~20 s per day | ~9 h |
| `drift` | ~4 min per day | ~5 days |
| `risk` | < 1 s per day | minutes |

Drift dominates and is CPU-bound, so it scales with workers: six bring the
whole backfill to about a day.

## The drift model

Each release day, fishing effort becomes carcasses: effort hours weighted by
gear (trawlers and set gillnets highest), by month (winter highest) and by the
share of dead animals that float. Each seed is split into particles that
[OpenDrift](https://opendrift.github.io/) `OceanDrift` carries for 20 days with
CMEMS IBI surface currents (1/36°, hourly), CMEMS Stokes drift, a windage of
1.2 % ± 0.4 % of the wind, and 25 m²/s of turbulent diffusion. A particle that
hits the GSHHG coastline strands there; its weight, decayed with a 12-day
half-life, is credited to the nearest segment within 15 km on the day it lands.

The CMEMS files live in `FORCING_DIR` (a volume in Compose) only as long as a
drift day still needs them: after each drift job, files no drift job is waiting
on and older than the 20-day re-run window are deleted. Re-running old drift
days (`--kind drift --redo`) therefore needs `--kind cmems --redo` over the same
span first.

Each CMEMS chunk takes the best dataset that covers it: the multi-year
reanalysis when it reaches that far, the analysis-forecast (currents, waves) or
near-real-time (wind) product otherwise. The chunks nearest today are re-opened
on every schedule, so the forecast part is refreshed.

## The risk model

Six signals feed a logistic model over a seasonal baseline built from the
stranding record, leaving out the year being predicted:

`persistence` (recent nearby strandings, decayed) · `drift_index` (what
OpenDrift landed here over the last three days) · `swell_m` · `wave_m` · `onshore_m` (wave push
square-on to the shore) · `period_s`

Each goes through the same fixed transform, `log1p(x / scale)`. The scale is
global and fitted once, **not** a per-day ranking — a flat-calm day and a storm
day are meant to score differently, which per-day ranking would erase.

`segment_risk` stores the untransformed signals alongside the probability, so
refitting reads straight from that table and only `probability` has to be
recomputed afterwards.

### The map index

The map draws one number per segment: a **stranding index**, 0–100. It is a
fixed monotone rescale of `probability`, so it can never disagree with the model,
and the scale does not move from day to day — the same colour means the same
absolute chance whenever you look. That is the point: a share of the day's peak,
which is what the map used to show, makes a flat-calm day and a storm day look
identical.

The scale is anchored to `segment_risk` itself, cached for ten minutes: the
median stored probability is 0 and the 99.9th percentile is 100, log-spaced
between them. So an ordinary segment-day reads 0 and only a genuinely elevated
one climbs. Ranking within the distribution instead would spread segment-days
evenly over 0-100 by construction, and a quiet day on an ordinary stretch of
coast would report something like 74 purely for being above the median.

Measuring the anchors from the data rather than pinning them to the coefficients
means the scale always describes the model that produced the numbers on screen,
and cannot go stale behind a refit. The index is computed at request time; no
column stores it.

Whether a calm day *looks* calm is a property of the model, not of the scale — a
monotone rescale cannot create contrast that the probabilities do not have. `fit`
logs the between-day and within-day spread so you can see which dominates.

### Before it is fitted

`coefficients.json` ships **zeroed**, and an unfitted model does not use the
signals at all: `risk` stores them, but reports the seasonal climatology as the
probability, and the map says so.

Until then the index answers "how strand-prone is this stretch of coast, for the
time of year" — not "how bad is today". The weather cannot move it, because
nothing has been calibrated to say by how much.

That is deliberate. The obvious alternative — shipping the previous model's
weights as a starting point — produces nonsense, because those were fitted
against per-day ranks in `[0, 1]` and this model feeds them `log1p(signal /
scale)`. With `persistence` at 12.7 that one term contributes `+6.3`, enough to
cancel the intercept on its own and report a 78% chance of a stranding. A number
that wrong is worse than no number.

### Fitting

After the first real backfill:

```bash
strandarr fit                                              # refit from segment_risk + strandings
strandarr schedule --start 2022-01-01 --kind risk --redo   # apply it to stored days
strandarr skill                                            # AUC of stored predictions
```

`fit` writes to `COEFFICIENTS_PATH`, which Compose points at a volume shared by
the worker and the web service — otherwise a rebuilt image would discard the fit.
The web service reads the file at start-up, so restart it after a refit.

`fit` keeps every segment-day a stranding happened on and samples 20 negatives
per positive, then corrects the intercept back to the true base rate.

## Layout

```
strandarr/
  config.py log.py timeframe.py errors.py    settings, logging, day spans
  db.py                                      engine, session, upsert/replace
  jobs.py                                    the registry, scheduler and worker
  cli.py  web.py                             commands, and the read-only API
  models/                                    one file per table (10 tables)
  analysis/  geo.py drift.py risk.py         the region, the simulation, the model
  sources/   http.py + one file per source   everything that talks to the outside
  static/                                    MapLibre map, no build step
  coefficients.json                          fitted weights and the index scale
  grid_points.json                           the cells worth sampling
scripts/
  build-grid.py                              rebuild the cells from coastline + bathymetry
  check-schedule.py                          prove the scheduler leaves no gaps
  backup-db.sh restore-db.sh                 dump and restore
```

| table | holds |
|---|---|
| `coastal_segment` | ~10 km pieces of the French Atlantic and Channel coast |
| `cell` | the 0.25° cells wind and sea state are stored on |
| `wind` | hourly wind per cell: CMEMS L4, then the Open-Meteo forecast |
| `sea` | hourly currents, waves and swell per cell, averaged from CMEMS IBI |
| `vessel` | one row per fishing vessel (MMSI, name, flag, gear) |
| `vessel_position` | hourly positions: GFW fishing effort and live AIS |
| `stranding` | Pelagis records, via GBIF (to 2022) and histo-carto (2023 on) |
| `drift_daily` | what each release day landed on each segment, per landing day |
| `segment_risk` | the probability and its signals, per segment per day |
| `job` | the queue |

`grid_points.json` decides which cells are sampled: sea cells within
`MAX_DISTANCE_TO_COAST_KM`, plus land cells within `COASTAL_LAND_MARGIN_KM`.

## Development

```bash
make all-checks      # ruff lint + format check + mypy strict
make db-revision MSG='what changed'
make db-upgrade
make db-check        # fail if the models drifted from the migrations
```
