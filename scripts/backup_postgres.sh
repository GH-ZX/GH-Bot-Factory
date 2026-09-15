#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] && set -a && source .env && set +a
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BASE="$BACKUP_DIR/ghbf-$STAMP.dump"
TMP="$BASE.tmp"
cleanup(){ rm -f "$TMP"; }
trap cleanup EXIT

docker compose exec -T postgres sh -lc 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$TMP"
[[ -s "$TMP" ]] || { echo "backup is empty" >&2; exit 1; }
mv "$TMP" "$BASE"
sha256sum "$BASE" > "$BASE.sha256"

if [[ -n "${BACKUP_GPG_RECIPIENT:-}" ]]; then
  gpg --batch --yes --trust-model always --recipient "$BACKUP_GPG_RECIPIENT" --encrypt "$BASE"
  sha256sum "$BASE.gpg" > "$BASE.gpg.sha256"
  rm -f "$BASE" "$BASE.sha256"
  BASE="$BASE.gpg"
elif [[ "${APP_ENV:-development}" == "production" && "${ALLOW_UNENCRYPTED_BACKUPS:-false}" != "true" ]]; then
  echo "Production backups require BACKUP_GPG_RECIPIENT or explicit ALLOW_UNENCRYPTED_BACKUPS=true" >&2
  rm -f "$BASE" "$BASE.sha256"
  exit 1
fi

find "$BACKUP_DIR" -type f -name 'ghbf-*' -mtime "+${BACKUP_RETENTION_DAYS:-14}" -delete
printf '%s\n' "$BASE"
