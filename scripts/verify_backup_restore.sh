#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 1 ]] || { echo "Usage: $0 <backup.dump|backup.dump.gpg>" >&2; exit 2; }
FILE="$1"
[[ -f "$FILE" ]] || { echo "Backup not found: $FILE" >&2; exit 1; }
if [[ -f "$FILE.sha256" ]]; then
  (cd "$(dirname "$FILE")" && sha256sum -c "$(basename "$FILE").sha256")
fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] && set -a && source .env && set +a
DB="ghbf_restore_verify_$(date +%s)_$RANDOM"
cleanup(){ docker compose exec -T postgres sh -lc "PGPASSWORD=\"\$POSTGRES_PASSWORD\" dropdb -h 127.0.0.1 -U \"\$POSTGRES_USER\" --if-exists '$DB'" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker compose exec -T postgres sh -lc "PGPASSWORD=\"\$POSTGRES_PASSWORD\" createdb -h 127.0.0.1 -U \"\$POSTGRES_USER\" '$DB'"
if [[ "$FILE" == *.gpg ]]; then
  gpg --batch --decrypt "$FILE" | docker compose exec -T postgres sh -lc "PGPASSWORD=\"\$POSTGRES_PASSWORD\" pg_restore -h 127.0.0.1 -U \"\$POSTGRES_USER\" -d '$DB' --no-owner"
else
  cat "$FILE" | docker compose exec -T postgres sh -lc "PGPASSWORD=\"\$POSTGRES_PASSWORD\" pg_restore -h 127.0.0.1 -U \"\$POSTGRES_USER\" -d '$DB' --no-owner"
fi
docker compose exec -T postgres sh -lc "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -h 127.0.0.1 -U \"\$POSTGRES_USER\" -d '$DB' -v ON_ERROR_STOP=1 -c 'SELECT version_num FROM alembic_version;' -c 'SELECT count(*) AS tenant_count FROM tenants;'"
echo "Restore verification succeeded for $FILE"
