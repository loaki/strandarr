# strandarr

Cetacean stranding risk along the French coast, from fishing effort, sea state
and the stranding record itself.

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
because that is how long its forcing window takes to be fully archived. It is
deliberately blunt — it can recompute more than needed, never less.

| kind | what it does | window |
|---|---|---|
| `gfw` | fishing effort per vessel-hour | from 2012, 4-day lag |
| `weather_archive` | ERA5 wind | from 1940, 5-day lag, 14-day chunks |
| `marine_archive` | currents, waves, swell, tide | from 2022, 5-day lag, 14-day chunks |
| `forecast` | weather + marine ahead of now | rolling, re-run daily |
| `strandings_gbif` | Pelagis archive via GBIF | up to 2022, yearly chunks |
| `strandings_pelagis` | Pelagis histo-carto | from 2023, 30-day chunks |
| `drift` | particle simulation per release day | from 2022, 4-day lag |
| `risk` | probability per segment per day | from 2022, forecast days ahead |

## Setup

```bash
cp .env.example .env          # then fill in GFW_API_TOKEN and AISSTREAM_API_KEY
docker compose up -d          # db, init (migrate + coastline), web, worker, aisstream
```

`init` runs `alembic upgrade head` then `strandarr reference`, which fetches the
Natural Earth coastline and cuts it into ~10 km segments. Every prediction is
made for one of those segments, so this has to happen before anything else.

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

## The risk model

Six signals feed a logistic model over a seasonal baseline built from the
stranding record, leaving out the year being predicted:

`persistence` (recent nearby strandings, decayed) · `drift_index` (what the
drift model landed here) · `swell_m` · `wave_m` · `onshore_m` (wave push
square-on to the shore) · `period_s`

Each goes through the same fixed transform, `log1p(x / scale)`. The scale is
global and fitted once, **not** a per-day ranking — a flat-calm day and a storm
day are meant to score differently, which per-day ranking would erase.

`segment_risk` stores the untransformed signals alongside the probability, so
refitting reads straight from that table and only `probability` has to be
recomputed afterwards.

### Fitting

Coefficients live in `strandarr/coefficients.json` and ship **unfitted** — the
committed values are carried over from the previous model, whose inputs were
scaled differently. They are a starting point, not a calibration. After the
first real backfill:

```bash
strandarr fit                            # refit from segment_risk + strandings
strandarr schedule --start 2022-01-01    # recompute probabilities
strandarr skill                          # AUC of stored predictions
```

`fit` keeps every segment-day a stranding happened on and samples 20 negatives
per positive, then corrects the intercept back to the true base rate.

## Layout

```
strandarr/
  config.py log.py timeframe.py errors.py    settings, logging, day spans
  db.py                                      engine, session, upsert/replace
  jobs.py                                    the registry, scheduler and worker
  cli.py  web.py                             commands, and the read-only API
  models/                                    one file per table (7 tables)
  analysis/  geo.py drift.py risk.py         the region, the simulation, the model
  sources/   http.py + one file per source   everything that talks to the outside
  static/                                    MapLibre map, no build step
```

## Development

```bash
make all-checks      # ruff lint + format check + mypy strict
make db-revision MSG='what changed'
make db-upgrade
make db-check        # fail if the models drifted from the migrations
```
