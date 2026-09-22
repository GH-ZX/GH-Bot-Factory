#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ "${1:-}" != "--approved-and-backed-up" ]; then
  echo 'Read UPDATE-RESTORE.md. Customer approval and database + vault backups are required.' >&2
  echo 'Then set the approved immutable GHBF_IMAGE in .env and run sh update.sh --approved-and-backed-up' >&2
  exit 1
fi
# Compose is limited to this package project; no host-wide commands are used.
python3 preflight.py
python3 lifecycle.py begin update
docker compose stop api worker bot-runtime
docker compose pull
docker compose run --rm -T tools python -m scripts.customer_release_reference
docker compose run --rm -T tools python -m alembic upgrade head
python3 lifecycle.py finish
echo 'Migration completed. Services remain stopped for owner-requested acceptance.'
echo 'Start with: docker compose up -d redis api worker bot-runtime'
