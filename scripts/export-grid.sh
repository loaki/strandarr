#!/usr/bin/env bash
# Freeze the grid the live database already uses into strandarr/grid_points.json.
#
# Run this BEFORE `alembic upgrade head`, while the grid_cell table still exists.
# It captures exactly the cells the old code requested -- sea cells within reach
# of the shore, plus land cells close enough for the marine model to answer -- so
# the refactor keeps sampling the same cells and the ingest history stays valid.
set -euo pipefail

cd "$(dirname "$0")/.."

export OUT_FILE="${OUT_FILE:-strandarr/grid_points.json}"
export MAX_DISTANCE_KM="${MAX_DISTANCE_TO_COAST_KM:-200}"
export LAND_MARGIN_KM="${COASTAL_LAND_MARGIN_KM:-30}"

export GRID_ROWS="$(docker compose exec -T db sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" \
    -At -F ',' -c \"
      SELECT lat, lon FROM grid_cell
      WHERE distance_to_coast_km IS NOT NULL
        AND distance_to_coast_km <= ${MAX_DISTANCE_KM}
        AND (elevation_m < 0 OR distance_to_coast_km <= ${LAND_MARGIN_KM})
      ORDER BY lat, lon;\"")"

python3 - <<'PY'
import json
import os
import sys

rows = [line for line in os.environ["GRID_ROWS"].splitlines() if line.strip()]
if not rows:
    sys.exit("grid_cell returned no rows -- has the database already been migrated?")

points = [[float(lat), float(lon)] for lat, lon in (row.split(",") for row in rows)]
out = os.environ["OUT_FILE"]
with open(out, "w") as handle:
    json.dump(
        {
            "source": "grid_cell, exported before the refactor migration",
            "max_distance_to_coast_km": float(os.environ["MAX_DISTANCE_KM"]),
            "coastal_land_margin_km": float(os.environ["LAND_MARGIN_KM"]),
            "points": points,
        },
        handle,
    )
    handle.write("\n")
print(f"wrote {len(points)} grid point(s) to {out}")
PY
