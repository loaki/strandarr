#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_FILE="$BACKUP_DIR/strandarr_${TIMESTAMP}.dump"

mkdir -p "$BACKUP_DIR"

TMP_FILE="$(mktemp "${OUT_FILE}.XXXXXX")"
trap 'rm -f "$TMP_FILE"' EXIT

docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$TMP_FILE"

docker compose exec -T db sh -c 'pg_restore --list' < "$TMP_FILE" > /dev/null

mv "$TMP_FILE" "$OUT_FILE"
trap - EXIT

find "$BACKUP_DIR" -name 'strandarr_*.dump' -mtime "+${RETENTION_DAYS}" -delete

echo "Backup written to $OUT_FILE"
