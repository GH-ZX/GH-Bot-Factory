#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] && set -a && source .env && set +a

EXPORT_DIR="${PORTABLE_EXPORT_DIR:-$ROOT/portable-exports}"
GPG_RECIPIENT="${PORTABLE_GPG_RECIPIENT:-}"
ALLOW_PLAINTEXT="${ALLOW_PLAINTEXT_PORTABLE_EXPORT:-false}"

usage() {
  echo "Usage: $0 [--output-dir DIR] [--gpg-recipient RECIPIENT] [--allow-plaintext]" >&2
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-dir) EXPORT_DIR="$2"; shift 2 ;;
    --gpg-recipient) GPG_RECIPIENT="$2"; shift 2 ;;
    --allow-plaintext) ALLOW_PLAINTEXT="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
docker compose version >/dev/null
if [[ -z "$GPG_RECIPIENT" && "$ALLOW_PLAINTEXT" != "true" ]]; then
  echo "Portable bundles contain the encrypted vault master key." >&2
  echo "Set PORTABLE_GPG_RECIPIENT/use --gpg-recipient, or explicitly pass --allow-plaintext." >&2
  exit 1
fi
if [[ -n "$GPG_RECIPIENT" ]]; then
  command -v gpg >/dev/null || { echo "gpg is required for encrypted portable exports" >&2; exit 1; }
fi

mkdir -p "$EXPORT_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK="$(mktemp -d "$EXPORT_DIR/.ghbf-portable-$STAMP.XXXXXX")"
ARCHIVE="$EXPORT_DIR/ghbf-portable-$STAMP.tar.gz"
RUNNING_FILE="$WORK/running-services.txt"

cleanup() {
  rm -rf "$WORK"
}
trap cleanup EXIT

docker compose ps --services --status running > "$RUNNING_FILE"
restart_previous() {
  local services=()
  for svc in api worker bot-runtime; do
    if grep -qx "$svc" "$RUNNING_FILE"; then services+=("$svc"); fi
  done
  if [[ ${#services[@]} -gt 0 ]]; then
    docker compose up -d "${services[@]}" >/dev/null
  fi
}
trap 'restart_previous; cleanup' EXIT

for svc in api worker bot-runtime; do
  if grep -qx "$svc" "$RUNNING_FILE"; then docker compose stop "$svc" >/dev/null; fi
done

mkdir -p "$WORK/state"
echo "[GHBF] Exporting PostgreSQL..."
docker compose exec -T postgres sh -lc \
  'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > "$WORK/state/postgres.dump"
[[ -s "$WORK/state/postgres.dump" ]] || { echo "PostgreSQL export is empty" >&2; exit 1; }

echo "[GHBF] Exporting encrypted local secret vault..."
docker compose run --rm --no-deps -T api python -c '
import sys, tarfile
from pathlib import Path
root = Path("/var/lib/ghbf/secret-store")
with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as archive:
    if root.exists():
        for path in sorted(root.rglob("*")):
            archive.add(path, arcname=str(Path("secret-store") / path.relative_to(root)), recursive=False)
' > "$WORK/state/secret-store.tar.gz"
[[ -s "$WORK/state/secret-store.tar.gz" ]] || { echo "Secret vault export is empty" >&2; exit 1; }

GIT_SHA="unknown"
if command -v git >/dev/null && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  GIT_SHA="$(git rev-parse HEAD)"
fi
MIGRATION_HEAD="$(docker compose run --rm --no-deps -T api python -m alembic heads 2>/dev/null | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
python3 - "$WORK/state/manifest.json" "$STAMP" "$GIT_SHA" "$MIGRATION_HEAD" <<'PY'
import json, sys
from pathlib import Path
path, stamp, git_sha, migration_head = sys.argv[1:]
payload = {
    "format": "ghbf-portable-state-v1",
    "created_at_utc": stamp,
    "git_sha": git_sha,
    "migration_head": migration_head,
    "contents": ["postgres.dump", "secret-store.tar.gz"],
    "includes_environment_file": False,
    "notes": "Runtime .env is intentionally excluded; destination host generates its own DB/JWT/platform credentials and public URLs.",
}
Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

(
  cd "$WORK/state"
  sha256sum postgres.dump secret-store.tar.gz manifest.json > SHA256SUMS
  tar -czf "$ARCHIVE" manifest.json SHA256SUMS postgres.dump secret-store.tar.gz
)
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"

if [[ -n "$GPG_RECIPIENT" ]]; then
  gpg --batch --yes --trust-model always --recipient "$GPG_RECIPIENT" --encrypt "$ARCHIVE"
  sha256sum "$ARCHIVE.gpg" > "$ARCHIVE.gpg.sha256"
  rm -f "$ARCHIVE" "$ARCHIVE.sha256"
  ARCHIVE="$ARCHIVE.gpg"
fi

restart_previous
trap cleanup EXIT
printf '%s\n' "$ARCHIVE"
