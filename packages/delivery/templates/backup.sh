#!/usr/bin/env bash
# Destination-only backup. Nothing is restored or restarted automatically.
set -euo pipefail
umask 077
cd "$(dirname "$0")"
if [[ "${1:-}" != "--approved-stop-writes" || -z "${2:-}" || -z "${PGSERVICE:-}" ]]; then
  echo 'Usage: PGSERVICE=customer-db bash backup.sh --approved-stop-writes GPG_RECIPIENT' >&2
  echo 'Read UPDATE-RESTORE.md. The PostgreSQL service must point to this installation database.' >&2
  exit 1
fi
for program in docker python3 pg_dump gpg; do
  command -v "$program" >/dev/null || { echo "Required program unavailable: $program" >&2; exit 1; }
done
python3 preflight.py
mkdir -p backups
work="$(mktemp -d backups/.backup-XXXXXXXX)"
trap 'rm -rf "$work"' EXIT
mkdir .operation-lock || { echo 'Another installation/update/backup operation requires attention.' >&2; exit 1; }
trap 'rm -rf "$work"; rmdir .operation-lock' EXIT
stamp="$(date -u +%Y%m%dT%H%M%SZ)-${RANDOM}"
output="backups/$stamp"
mkdir "$output"
# Keep services stopped on every failure so the backup state is not misrepresented.
docker compose stop api worker bot-runtime
if ! pg_dump --format=custom --no-password > "$work/database.dump" 2> "$work/database-error.log"; then
  echo 'Database backup failed. Check the private PostgreSQL service configuration. Applications remain stopped.' >&2
  exit 1
fi
[[ -s "$work/database.dump" ]] || { echo 'Database backup is empty.' >&2; exit 1; }
docker compose run --rm --no-deps -T tools python -c '
import sys, tarfile
from pathlib import Path
root = Path("/var/lib/ghbf/secret-store")
if not root.is_dir() or not any(root.iterdir()):
    raise SystemExit("Secret vault is missing or empty; backup refused.")
with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as archive:
    archive.add(root, arcname="secret-store")
' > "$work/vault.tar.gz"
gpg --batch --recipient "$2" --output "$output/database.dump.gpg" --encrypt "$work/database.dump"
gpg --batch --recipient "$2" --output "$output/vault.tar.gz.gpg" --encrypt "$work/vault.tar.gz"
python3 - "$output" <<'PY'
import hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path
root = Path(sys.argv[1])
receipt = {'status': 'COMPLETED', 'backup_completed_at': datetime.now(timezone.utc).isoformat(),
           'backup_reference': str(root), 'restore_acceptance': 'not_performed'}
for key, filename in [('database_backup_sha256', 'database.dump.gpg'), ('vault_backup_sha256', 'vault.tar.gz.gpg')]:
    with (root / filename).open('rb') as stream:
        receipt[key] = hashlib.file_digest(stream, 'sha256').hexdigest()
(root / 'backup-receipt.json').write_text(json.dumps(receipt, indent=2))
PY
echo "Encrypted database and vault backup written to $output. Applications remain stopped."
echo 'Copy the files off this server and record their checksums. A restore drill is still required.'
