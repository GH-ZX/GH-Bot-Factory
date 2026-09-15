#!/usr/bin/env bash
set -euo pipefail

PROJECT="${PROJECT_DIR:-$(pwd)}"

echo "==> Checking system..."
if ! command -v docker >/dev/null 2>&1; then
    echo "WARNING: Docker is not found in PATH."
fi
if ! command -v git >/dev/null 2>&1; then
    echo "ERROR: Git is not installed."
    exit 1
fi

echo "==> Ensuring directory structure in $PROJECT..."
cd "$PROJECT"

mkdir -p \
    apps/api \
    apps/bot-runtime \
    apps/worker \
    apps/admin \
    packages/core \
    packages/tenants \
    packages/commerce \
    packages/providers \
    packages/payments \
    packages/telegram \
    packages/factory \
    infra/docker \
    docs/architecture \
    docs/decisions \
    tests \
    .github/workflows \
    scripts

echo "==> Bootstrap completed successfully."
