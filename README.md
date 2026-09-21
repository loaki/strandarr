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

A chunked kind needs `volatile >= chunk + lag` for a second reason. Its job is
enqueued as soon as *any* of its days is old enough, and it ingests only the days
available when it runs, so without re-opening the trailing chunk would stay
partly ingested for ever while the job reads `done`.
`make check-schedule` replays the calendar and fails if any kind leaves a gap.

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

`strandarr status` shows what is queued, what failed, and what is waiting on
inputs. A job whose inputs never arrive is deferred, not retried, and is failed
after `MAX_DEFERRALS` so it cannot re-queue itself for ever.

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

`schedule` skips days already `done`. To compute a day *again* — after a refit,
or after changing the model — re-open it explicitly:

```bash
strandarr schedule --start 2022-01-01 --kind risk --redo
```

Jobs are claimed oldest day first, because drift for a day feeds the risk of the
days after it. A long backfill therefore runs ahead of the present: queue it in
stages (a year at a time) and let each drain, or the live map waits behind it.

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

### The map index

The map draws one number per segment: a **stranding index**, 0–100. It is a
fixed monotone rescale of `probability`, so it can never disagree with the model,
and the scale does not move from day to day — the same colour means the same
absolute chance whenever you look. That is the point: a share of the day's peak,
which is what the map used to show, makes a flat-calm day and a storm day look
identical.

The breakpoints are the probability quantiles measured from `segment_risk`
itself, cached for ten minutes, so the scale always describes the model that
produced the numbers on screen and cannot go stale behind a refit. The index is
computed at request time; no column stores it.

Whether a calm day *looks* calm is a property of the model, not of the scale — a
monotone rescale cannot create contrast that the probabilities do not have. `fit`
logs the between-day and within-day spread so you can see which dominates.

### Before it is fitted

`coefficients.json` ships **zeroed**, and an unfitted model does not use the
signals at all: `risk` stores them, but reports the seasonal climatology as the
probability, and the map says so.

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
  models/                                    one file per table (7 tables)
  analysis/  geo.py drift.py risk.py         the region, the simulation, the model
  sources/   http.py + one file per source   everything that talks to the outside
  static/                                    MapLibre map, no build step
  coefficients.json                          fitted weights and the index scale
  grid_points.json                           the cells worth requesting
scripts/
  export-grid.sh                             freeze the grid from a live grid_cell
  build-grid.py                              or rebuild it from coastline + bathymetry
  rehearse-migration.sh                      run the migration on a copy of production
  check-schedule.py                          prove the scheduler leaves no gaps
  backup-db.sh restore-db.sh                 dump and restore
```

`grid_points.json` decides which cells are requested: sea cells within
`MAX_DISTANCE_TO_COAST_KM`, plus land cells within `COASTAL_LAND_MARGIN_KM`.
Without it every cell within reach of the shore is sampled, inland ones included
— correct, but roughly twice the requests for rows the marine model cannot fill.
The worker warns when the file is missing.

## Upgrading a database from before the refactor

The migration chain is four revisions instead of twenty-two:

| revision | |
|---|---|
| `5f2b94d0e3a8` | the deployed schema, squashed — never runs on a live database |
| `6a3c72fb1e94` | carried over unchanged: `ingest_coverage` gains its measurements |
| `7b4d18e6c052` | carried over unchanged: `segment_forecast` + `segment_climatology` become `segment_risk` |
| `0004_refactor` | the refactor |

A live database joins wherever it is stamped and walks forward; a fresh install
runs all four and lands on exactly the same schema. **Check where yours is before
anything else** — `alembic current` — because the chain starts at `5f2b94d0e3a8`
and cannot migrate a database older than that. One older would need the
pre-refactor code to bring it up first.

`0004_refactor` keeps everything that cost an API call or a simulation. It merges
`marine_condition`'s four source rows per cell-hour into one `condition` row,
archived value first and forecast second — the precedence the old read path
applied on every query. It keeps `drift_daily` (the drift physics did not change),
`stranding`, `vessel_position` and `coastal_segment` with their ids intact, and it
seeds the `job` table from `ingest_coverage` so nothing already fetched is fetched
again. It drops `segment_risk`, because the new model needs two signals the old
one never stored and filling them with zeros would corrupt `strandarr fit`; those
rows are recomputed locally, with no API call. If your database predates
`7b4d18e6c052`, `segment_forecast` and `segment_climatology` go the same way on
the same reasoning.

```bash
scripts/backup-db.sh                      # this dump is the only way back
scripts/export-grid.sh                    # BEFORE migrating, while grid_cell exists

# rehearse on a copy, never on the live database
createdb rehearsal && pg_restore -d rehearsal --no-owner backups/strandarr_<ts>.dump
POSTGRES_DB=rehearsal POSTGRES_HOST=127.0.0.1 scripts/rehearse-migration.sh
```

Then rename `MARINE_FORECAST_HOURS` to `FORECAST_HOURS` in `.env` — unknown keys
are ignored rather than rejected, so a stale name fails silently — and bring the
stack up. Afterwards, backfill `risk` in stages and refit.

`downgrade()` raises: merging the condition sources cannot be undone from what is
left. Restore the dump.

If `init` exits with `Can't locate revision identified by '<id>'`, the database is
stamped at a revision this chain does not contain. Nothing has been changed --
alembic refuses before it writes anything. Check `alembic current` against the
table above.

## Development

```bash
make all-checks      # ruff lint + format check + mypy strict
make db-revision MSG='what changed'
make db-upgrade
make db-check        # fail if the models drifted from the migrations
```
