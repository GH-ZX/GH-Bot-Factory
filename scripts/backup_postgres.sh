#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

dotenv_get() {
  local key="$1"
  [[ -f .env ]] || return 0
  awk -v prefix="${key}=" '
    index($0, prefix) == 1 { value = substr($0, length(prefix) + 1); found = 1 }
    END { if (found) print value }
  ' .env
}

POSTGRES_DB="${POSTGRES_DB:-$(dotenv_get POSTGRES_DB)}"
POSTGRES_USER="${POSTGRES_USER:-$(dotenv_get POSTGRES_USER)}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(dotenv_get POSTGRES_PASSWORD)}"
BACKUP_DIR="${BACKUP_DIR:-$(dotenv_get BACKUP_DIR)}"
BACKUP_GPG_RECIPIENT="${BACKUP_GPG_RECIPIENT:-$(dotenv_get BACKUP_GPG_RECIPIENT)}"
APP_ENV="${APP_ENV:-$(dotenv_get APP_ENV)}"
ALLOW_UNENCRYPTED_BACKUPS="${ALLOW_UNENCRYPTED_BACKUPS:-$(dotenv_get ALLOW_UNENCRYPTED_BACKUPS)}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-$(dotenv_get BACKUP_RETENTION_DAYS)}"

POSTGRES_DB="${POSTGRES_DB:-gh_bot_factory}"
POSTGRES_USER="${POSTGRES_USER:-gh_bot_factory}"
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
(cd "$(dirname "$BASE")" && sha256sum "$(basename "$BASE")") > "$BASE.sha256"

if [[ -n "${BACKUP_GPG_RECIPIENT:-}" ]]; then
  gpg --batch --yes --trust-model always --recipient "$BACKUP_GPG_RECIPIENT" --encrypt "$BASE"
  (cd "$(dirname "$BASE")" && sha256sum "$(basename "$BASE").gpg") > "$BASE.gpg.sha256"
  rm -f "$BASE" "$BASE.sha256"
  BASE="$BASE.gpg"
elif [[ "${APP_ENV:-development}" == "production" && "${ALLOW_UNENCRYPTED_BACKUPS:-false}" != "true" ]]; then
  echo "Production backups require BACKUP_GPG_RECIPIENT or explicit ALLOW_UNENCRYPTED_BACKUPS=true" >&2
  rm -f "$BASE" "$BASE.sha256"
  exit 1
fi

find "$BACKUP_DIR" -type f -name 'ghbf-*' -mtime "+${BACKUP_RETENTION_DAYS:-14}" -delete
printf '%s\n' "$BASE"
