#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
: "${POSTGRES_TEST_DATABASE_URL:?POSTGRES_TEST_DATABASE_URL is required}"
mkdir -p artifacts
START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SHA="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
if ! git diff --quiet || ! git diff --cached --quiet || [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
  echo "Release gate requires a clean committed working tree so Git SHA matches the built artifact." >&2
  exit 1
fi
HEADS="$(python -m alembic heads | awk '{print $1}')"
if [[ "$(printf '%s\n' "$HEADS" | sed '/^$/d' | wc -l)" -ne 1 ]]; then
  echo "Release gate requires exactly one Alembic head; found: $HEADS" >&2
  exit 1
fi
HEAD_REV="$HEADS"

APP_ENV=test RATE_LIMIT_ENABLED=false ./scripts/verify.sh all
python scripts/security_scan.py
python -m pip_audit --strict

tmp_env="$(mktemp)"
trap 'rm -f "$tmp_env"' EXIT
cp .env.example "$tmp_env"
printf '\nPOSTGRES_PASSWORD=release-gate-only\nJWT_SECRET_KEY=%s\n' "$(python -c 'print("x"*48)')" >> "$tmp_env"
POSTGRES_PASSWORD=release-gate-only docker compose --env-file "$tmp_env" config >/dev/null
docker build --pull --build-arg VCS_REF="$SHA" -t "gh-bot-factory:release-$SHA" .
IMAGE_ID="$(docker image inspect "gh-bot-factory:release-$SHA" --format '{{.Id}}')"

if [[ "${RUN_STAGING_GATE:-false}" == "true" ]]; then
  [[ "${APP_ENV:-}" == "staging" ]] || { echo "RUN_STAGING_GATE requires APP_ENV=staging" >&2; exit 1; }
  python scripts/staging_e2e.py
  if [[ "${RUN_FAILURE_INJECTION:-false}" == "true" ]]; then
    ALLOW_FAILURE_INJECTION=I_UNDERSTAND ./scripts/staging_failure_injection.sh
  fi
fi

END="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cat > artifacts/release-evidence.json <<JSON
{"status":"passed","started_at":"$START","completed_at":"$END","git_sha":"$SHA","migration_head":"$HEAD_REV","image_id":"$IMAGE_ID","staging_gate":${RUN_STAGING_GATE:-false},"failure_injection":${RUN_FAILURE_INJECTION:-false}}
JSON
echo "Release gate passed; evidence: artifacts/release-evidence.json"
