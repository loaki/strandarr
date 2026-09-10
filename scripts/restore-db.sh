#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ $# -ne 1 ]; then
    echo "Usage: $0 <backup-file.dump>" >&2
    exit 1
fi

BACKUP_FILE="$1"
if [ ! -f "$BACKUP_FILE" ]; then
    echo "No such file: $BACKUP_FILE" >&2
    exit 1
fi

echo "This will DROP and replace all data in the running 'db' container's database."
read -r -p "Type 'yes' to continue: " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Aborted."
    exit 1
fi

restore() {
    docker compose exec -T db sh -c \
        'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --single-transaction --no-owner' \
        < "$BACKUP_FILE"
}

if restore; then
    echo "Restore complete."
else
    status=$?
    echo "Restore FAILED (exit $status). Leaving api stopped; database is unchanged." >&2
    exit "$status"
fi
