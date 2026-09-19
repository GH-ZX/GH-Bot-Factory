#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-all}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

run_fast() {
  command -v ruff >/dev/null 2>&1 || {
    echo "ruff is required for the canonical verification gate." >&2
    return 1
  }
  command -v node >/dev/null 2>&1 || {
    echo "node is required for the canonical verification gate." >&2
    return 1
  }

  ruff check .
  python -m compileall -q apps packages scripts tests migrations
  python scripts/check_handoff_consistency.py
  python scripts/security_scan.py

  local pytest_args=(-m "not postgres" -q)
  if [[ "${CI:-}" == "true" ]]; then
    mkdir -p artifacts
    pytest_args+=(--junitxml=artifacts/pytest-fast.xml)
  fi
  python -m pytest "${pytest_args[@]}"

  node --check apps/admin/static/app.js
  node --check apps/admin/static/store-setup.js
  node --check apps/miniapp/static/app.js
  node --check apps/configurator/static/app.js
  git diff --check
  python -m pip check
}

run_postgres() {
  : "${POSTGRES_TEST_DATABASE_URL:?POSTGRES_TEST_DATABASE_URL must be set for PostgreSQL verification}"
  export DATABASE_URL="$POSTGRES_TEST_DATABASE_URL"

  python - <<'PY'
import os
from urllib.parse import urlparse
url = os.environ["POSTGRES_TEST_DATABASE_URL"]
if not url.startswith("postgresql+asyncpg://"):
    raise SystemExit("POSTGRES_TEST_DATABASE_URL must use postgresql+asyncpg://")
db_name = urlparse(url.replace("postgresql+asyncpg://", "postgresql://", 1)).path.lstrip("/").lower()
if "test" not in db_name and os.environ.get("ALLOW_NONTEST_POSTGRES_VERIFY") != "I_UNDERSTAND":
    raise SystemExit(
        "Refusing PostgreSQL verification against a database whose name does not contain 'test'. "
        "Set ALLOW_NONTEST_POSTGRES_VERIFY=I_UNDERSTAND only for an isolated disposable database."
    )
PY

  python -m alembic upgrade head
  python -m alembic check

  local pytest_args=(-m postgres tests/postgres -q)
  if [[ "${CI:-}" == "true" ]]; then
    mkdir -p artifacts
    pytest_args+=(--junitxml=artifacts/pytest-postgres.xml)
  fi
  python -m pytest "${pytest_args[@]}"
}

case "$MODE" in
  fast)
    run_fast
    ;;
  postgres)
    run_postgres
    ;;
  all)
    run_fast
    run_postgres
    ;;
  *)
    echo "Usage: $0 [fast|postgres|all]" >&2
    exit 2
    ;;
esac
