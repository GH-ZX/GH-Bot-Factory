#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 2 || "$1" != "--confirm" ]]; then
  echo "Usage: $0 --confirm <backup.dump|backup.dump.gpg>" >&2
  exit 2
fi
FILE="$2"
[[ -f "$FILE" ]] || { echo "Backup not found: $FILE" >&2; exit 1; }
if [[ -f "$FILE.sha256" ]]; then
  (cd "$(dirname "$FILE")" && sha256sum -c "$(basename "$FILE").sha256")
fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] && set -a && source .env && set +a
if [[ "${APP_ENV:-development}" == "production" && "${RESTORE_ACKNOWLEDGE_DATA_LOSS:-}" != "I_UNDERSTAND" ]]; then
  echo "Set RESTORE_ACKNOWLEDGE_DATA_LOSS=I_UNDERSTAND for production restore." >&2
  exit 1
fi
STREAM=(cat "$FILE")
if [[ "$FILE" == *.gpg ]]; then STREAM=(gpg --batch --decrypt "$FILE"); fi
"${STREAM[@]}" | docker compose exec -T postgres sh -lc 'PGPASSWORD="$POSTGRES_PASSWORD" pg_restore -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner'
docker compose run --rm migrate
