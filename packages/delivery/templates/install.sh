#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ "${1:-}" != "--source-stopped-empty-destination" ]; then
  echo 'Read SETUP.md, initialize the empty database, and stop source writes/runtime first.' >&2
  echo 'Then run: sh install.sh --source-stopped-empty-destination' >&2
  exit 1
fi
[ -f .env ] || { echo 'Run python3 configure.py first.' >&2; exit 1; }
python3 preflight.py
python3 lifecycle.py begin install
docker compose pull
docker compose run --rm -T tools python -m scripts.customer_package_identity /handoff/manifest.json
docker compose run --rm tools python -m scripts.import_tenant_bundle /handoff/tenant.ghbf.enc --confirm-empty-offline-destination --activate --confirm-source-stopped
python3 lifecycle.py finish
echo 'Import complete. Review destination settings and owner sign-in before starting services.'
echo 'Start with: docker compose up -d redis api worker bot-runtime'
