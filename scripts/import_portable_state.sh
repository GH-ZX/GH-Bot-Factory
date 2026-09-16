#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || "$1" != "--confirm" ]]; then
  echo "Usage: $0 --confirm <ghbf-portable-*.tar.gz|.tar.gz.gpg>" >&2
  exit 2
fi
SOURCE="$2"
[[ -f "$SOURCE" ]] || { echo "Portable bundle not found: $SOURCE" >&2; exit 1; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] || { echo ".env must be configured on the destination host first (run scripts/easy_start.py once if needed)." >&2; exit 1; }
set -a && source .env && set +a

command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
docker compose version >/dev/null
if [[ "$SOURCE" == *.gpg ]]; then command -v gpg >/dev/null || { echo "gpg is required" >&2; exit 1; }; fi
if [[ -f "$SOURCE.sha256" ]]; then
  (cd "$(dirname "$SOURCE")" && sha256sum -c "$(basename "$SOURCE").sha256")
fi

WORK="$(mktemp -d)"
RUNNING_FILE="$WORK/running-services.txt"
cleanup(){ rm -rf "$WORK"; }
trap cleanup EXIT

ARCHIVE="$SOURCE"
if [[ "$SOURCE" == *.gpg ]]; then
  ARCHIVE="$WORK/portable.tar.gz"
  gpg --batch --decrypt "$SOURCE" > "$ARCHIVE"
fi

python3 - "$ARCHIVE" "$WORK/state" <<'PY'
import shutil, sys, tarfile
from pathlib import Path
archive_path, destination = sys.argv[1:]
dest = Path(destination).resolve()
dest.mkdir(parents=True, exist_ok=True)
with tarfile.open(archive_path, "r:gz") as archive:
    for member in archive.getmembers():
        name = Path(member.name)
        if name.is_absolute() or ".." in name.parts or member.issym() or member.islnk():
            raise SystemExit(f"Unsafe archive member: {member.name}")
        target = (dest / name).resolve()
        if dest not in target.parents and target != dest:
            raise SystemExit(f"Unsafe archive path: {member.name}")
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        elif member.isfile():
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise SystemExit(f"Cannot read archive member: {member.name}")
            with open(target, "wb") as handle:
                shutil.copyfileobj(source, handle)
        else:
            raise SystemExit(f"Unsupported archive member type: {member.name}")
PY

for required in manifest.json SHA256SUMS postgres.dump secret-store.tar.gz; do
  [[ -f "$WORK/state/$required" ]] || { echo "Portable bundle missing $required" >&2; exit 1; }
done
(
  cd "$WORK/state"
  sha256sum -c SHA256SUMS
)
python3 - "$WORK/state/manifest.json" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
if payload.get("format") != "ghbf-portable-state-v1":
    raise SystemExit("Unsupported GHBF portable bundle format")
print(f"[GHBF] Importing bundle created {payload.get('created_at_utc')} at migration {payload.get('migration_head')}")
PY

# Validate the entire inner vault archive before stopping services or replacing state.
python3 - "$WORK/state/secret-store.tar.gz" <<'VAULTCHECK'
import sys, tarfile
from pathlib import PurePosixPath
with tarfile.open(sys.argv[1], "r:gz") as archive:
    seen = set()
    for member in archive:
        path = PurePosixPath(member.name)
        if (path.is_absolute() or ".." in path.parts or not path.parts
                or path.parts[0] != "secret-store" or not (member.isdir() or member.isfile())
                or path in seen):
            raise SystemExit("Unsafe or duplicate secret vault archive entry")
        seen.add(path)
        if member.isfile():
            with archive.extractfile(member) as source:
                while source.read(1024 * 1024):
                    pass
VAULTCHECK

docker compose up -d postgres redis >/dev/null
docker compose ps --services --status running > "$RUNNING_FILE"
restart_previous() {
  local services=()
  for svc in api worker bot-runtime; do
    if grep -qx "$svc" "$RUNNING_FILE"; then services+=("$svc"); fi
  done
  if [[ ${#services[@]} -gt 0 ]]; then docker compose up -d "${services[@]}" >/dev/null; fi
}
finish_import() {
  local result=$?
  if [[ "$result" -ne 0 ]]; then
    echo "[GHBF] Import failed. Application services remain stopped; recover and validate database/vault state before restarting." >&2
  fi
  cleanup
  return "$result"
}
trap finish_import EXIT
for svc in api worker bot-runtime; do
  if grep -qx "$svc" "$RUNNING_FILE"; then docker compose stop "$svc" >/dev/null; fi
done

echo "[GHBF] Restoring PostgreSQL..."
cat "$WORK/state/postgres.dump" | docker compose exec -T postgres sh -lc \
  'PGPASSWORD="$POSTGRES_PASSWORD" pg_restore -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --single-transaction --exit-on-error'

echo "[GHBF] Restoring encrypted local secret vault..."
cat "$WORK/state/secret-store.tar.gz" | docker compose run --rm --no-deps -T api python -c '
import shutil, sys, tarfile
from pathlib import Path
root = Path("/var/lib/ghbf/secret-store")
root.mkdir(parents=True, exist_ok=True)
for child in list(root.iterdir()):
    if child.is_dir(): shutil.rmtree(child)
    else: child.unlink()
with tarfile.open(fileobj=sys.stdin.buffer, mode="r|gz") as archive:
    for member in archive:
        name = Path(member.name)
        if not name.parts or name.parts[0] != "secret-store" or ".." in name.parts:
            raise SystemExit(f"Unsafe secret vault archive path: {member.name}")
        relative = Path(*name.parts[1:])
        if not relative.parts:
            continue
        target = root / relative
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        elif member.isfile():
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None: raise SystemExit("Cannot read vault member")
            with open(target, "wb") as handle: shutil.copyfileobj(source, handle)
root.chmod(0o700)
for path in root.rglob("*"):
    try: path.chmod(0o700 if path.is_dir() else 0o600)
    except OSError: pass
'

docker compose run --rm migrate
restart_previous
trap cleanup EXIT

echo "[GHBF] Portable state import complete. Destination .env/public URLs remain destination-owned."
