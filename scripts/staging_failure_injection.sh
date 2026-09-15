#!/usr/bin/env bash
set -euo pipefail
[[ "${APP_ENV:-}" == "staging" ]] || { echo "APP_ENV=staging required" >&2; exit 1; }
[[ "${ALLOW_FAILURE_INJECTION:-}" == "I_UNDERSTAND" ]] || { echo "Set ALLOW_FAILURE_INJECTION=I_UNDERSTAND" >&2; exit 1; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
wait_healthy(){
  local service="$1"
  for _ in $(seq 1 30); do
    status="$(docker compose ps --format json "$service" 2>/dev/null | grep -o '"Health":"[^"]*"' | head -1 || true)"
    [[ "$status" == *healthy* ]] && return 0
    sleep 2
  done
  docker compose ps
  return 1
}

python scripts/staging_e2e.py

echo "Injecting worker crash/restart"
docker compose kill worker
docker compose up -d worker
wait_healthy worker

echo "Injecting Redis outage"
docker compose stop redis
sleep 3
docker compose start redis
wait_healthy redis
wait_healthy api

echo "Injecting PostgreSQL outage"
docker compose stop postgres
sleep 3
docker compose start postgres
wait_healthy postgres
wait_healthy api

python scripts/staging_e2e.py
echo "staging failure injection passed"
