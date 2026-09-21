#!/usr/bin/env bash
# Run the refactor migration against a copy of production and check what survived.
#
#   scripts/backup-db.sh
#   createdb rehearsal && pg_restore -d rehearsal --no-owner backups/strandarr_<ts>.dump
#   POSTGRES_DB=rehearsal POSTGRES_HOST=127.0.0.1 scripts/rehearse-migration.sh
#
# The counts come from the database in front of it, not from fixtures, so this
# works on the real dump. It never touches the live database unless you point
# POSTGRES_DB at it -- don't.
set -euo pipefail

cd "$(dirname "$0")/.."

PY="${PY:-uv run python}"
psql_at() { psql "postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}" -At -c "$1"; }

: "${POSTGRES_USER:?set POSTGRES_USER}" "${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD}"
: "${POSTGRES_DB:?set POSTGRES_DB}" "${POSTGRES_HOST:?set POSTGRES_HOST}"
export POSTGRES_PORT="${POSTGRES_PORT:-5432}"

echo "### before"
at="$($PY -m alembic current 2>/dev/null | tail -1)"
echo "    revision: $at"
case "$at" in
    # Any revision the chain still knows how to walk forward from.
    5f2b94d0e3a8*|6a3c72fb1e94*|7b4d18e6c052*) ;;
    0004_refactor*) echo "    already migrated; nothing to rehearse" >&2; exit 1 ;;
    "")             echo "    no alembic_version: this is not a strandarr database" >&2; exit 1 ;;
    *)
        echo "    '$at' is not in this chain. The refactor squashed the history into" >&2
        echo "    four revisions starting at 5f2b94d0e3a8; a database older than that" >&2
        echo "    has to be brought up to it with the pre-refactor code first." >&2
        exit 1 ;;
esac

before_cells="$(psql_at "SELECT count(DISTINCT (valid_at, lat, lon)) FROM marine_condition")"
before_wind="$(psql_at "SELECT count(*) FROM marine_condition WHERE source LIKE 'weather_%' AND wind_speed_kmh IS NOT NULL")"
before_segments="$(psql_at "SELECT count(*) FROM coastal_segment")"
before_strandings="$(psql_at "SELECT count(*) FROM stranding")"
before_vessels="$(psql_at "SELECT count(*) FROM vessel_position")"
before_drift="$(psql_at "SELECT count(DISTINCT (release_day, day, coastal_segment_id)) FROM drift_daily")"
printf '    marine_condition cell-hours %s, segments %s, strandings %s, vessels %s, drift keys %s\n' \
    "$before_cells" "$before_segments" "$before_strandings" "$before_vessels" "$before_drift"

if [ ! -f strandarr/grid_points.json ]; then
    echo "    NOTE: strandarr/grid_points.json is missing -- run scripts/export-grid.sh first," >&2
    echo "          or the worker will sample inland cells the old code skipped." >&2
fi

echo
echo "### migrating"
time $PY -m alembic upgrade head

echo
echo "### after"
fail=0
expect() {
    got="$(psql_at "$2")"
    if [ "$got" = "$3" ]; then printf '  ok   %-46s %s\n' "$1" "$got"
    else printf '  FAIL %-46s got %s, want %s\n' "$1" "$got" "$3"; fail=1; fi
}
expect "every cell-hour kept, none duplicated" "SELECT count(*) FROM condition" "$before_cells"
expect "segments kept"        "SELECT count(*) FROM coastal_segment" "$before_segments"
expect "strandings kept"      "SELECT count(*) FROM stranding" "$before_strandings"
expect "vessel positions kept" "SELECT count(*) FROM vessel_position" "$before_vessels"
expect "drift keys kept"      "SELECT count(*) FROM drift_daily" "$before_drift"
expect "no orphaned drift rows" \
    "SELECT count(*) FROM drift_daily d LEFT JOIN coastal_segment s ON s.id=d.coastal_segment_id WHERE s.id IS NULL" 0
expect "wind survived the merge" \
    "SELECT CASE WHEN count(*) > 0 THEN 'yes' ELSE 'no' END FROM condition WHERE wind_speed_kmh IS NOT NULL" \
    "$([ "$before_wind" -gt 0 ] && echo yes || echo no)"

echo "  --   seeded jobs (these days will not be requested again):"
psql_at "SELECT '       ' || kind || ' x' || count(*) || ' ' || min(day) || '..' || max(day)
         FROM job GROUP BY kind ORDER BY kind" || true

echo
echo "### models vs migrations"
$PY -m alembic check

echo
[ "$fail" = 0 ] && echo "REHEARSAL PASSED" || { echo "REHEARSAL FAILED"; exit 1; }
