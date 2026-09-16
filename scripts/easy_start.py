#!/usr/bin/env python3
"""One-command local/self-hosted first-run launcher for GH Bot Factory.

Creates missing local secrets, repairs DATABASE_URL safely, builds the Docker stack,
waits for the API readiness endpoint, and prints the first-run web installer URL.
It never prints database/JWT secrets or Telegram tokens.
"""

from __future__ import annotations

import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
EXAMPLE_PATH = ROOT / ".env.example"


def _read_env_lines() -> list[str]:
    if ENV_PATH.exists():
        return ENV_PATH.read_text(encoding="utf-8").splitlines()
    if EXAMPLE_PATH.exists():
        return EXAMPLE_PATH.read_text(encoding="utf-8").splitlines()
    return []


def _values(lines: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _set_value(lines: list[str], key: str, value: str) -> None:
    prefix = f"{key}="
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = f"{key}={value}"
            return
    if lines and lines[-1].strip():
        lines.append("")
    lines.append(f"{key}={value}")


def prepare_env() -> str:
    lines = _read_env_lines()
    values = _values(lines)

    db_name = values.get("POSTGRES_DB") or "gh_bot_factory"
    db_user = values.get("POSTGRES_USER") or "gh_bot_factory"
    db_password = values.get("POSTGRES_PASSWORD") or secrets.token_hex(24)
    jwt_secret = values.get("JWT_SECRET_KEY") or secrets.token_urlsafe(48)
    setup_code = values.get("SETUP_CODE") or secrets.token_urlsafe(18)
    platform_admin_token = values.get("PLATFORM_ADMIN_TOKEN") or secrets.token_urlsafe(48)
    api_port = values.get("API_HOST_PORT") or "8010"

    encoded_user = urllib.parse.quote(db_user, safe="")
    encoded_password = urllib.parse.quote(db_password, safe="")
    encoded_db = urllib.parse.quote(db_name, safe="")
    database_url = (
        f"postgresql+asyncpg://{encoded_user}:{encoded_password}@postgres:5432/{encoded_db}"
    )

    defaults = {
        "APP_ENV": values.get("APP_ENV") or "development",
        "POSTGRES_DB": db_name,
        "POSTGRES_USER": db_user,
        "POSTGRES_PASSWORD": db_password,
        "DATABASE_URL": database_url,
        "REDIS_URL": values.get("REDIS_URL") or "redis://redis:6379/0",
        "JWT_SECRET_KEY": jwt_secret,
        "SETUP_CODE": setup_code,
        "PLATFORM_ADMIN_TOKEN": platform_admin_token,
        "LOCAL_SECRET_VAULT_ENABLED": "true",
        "LOCAL_SECRET_VAULT_DIR": "/var/lib/ghbf/secret-store",
        "API_HOST_PORT": api_port,
    }
    for key, value in defaults.items():
        _set_value(lines, key, value)

    ENV_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    try:
        ENV_PATH.chmod(0o600)
    except OSError:
        pass
    return setup_code


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def wait_for_api(port: str, timeout_seconds: int = 120) -> None:
    url = f"http://127.0.0.1:{port}/health/ready"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError):
            # During container startup the host socket can briefly accept and then
            # reset/refuse the connection while Uvicorn is still initializing.
            # Treat those transport errors as transient until the readiness deadline.
            pass
        time.sleep(2)
    raise RuntimeError(
        "API did not become ready in time. Run: docker compose logs --tail=200 api migrate postgres redis"
    )


def main() -> None:
    if shutil.which("docker") is None:
        raise SystemExit("Docker is required. Install Docker Engine + Compose plugin first.")
    try:
        run("docker", "compose", "version")
    except subprocess.CalledProcessError as exc:
        raise SystemExit("Docker Compose plugin is required.") from exc

    setup_code = prepare_env()
    values = _values(_read_env_lines())
    port = values.get("API_HOST_PORT", "8010")

    print("\n[GHBF] Building and starting PostgreSQL, Redis, migrations, API, worker, and bot runtime…")
    run(
        "docker",
        "compose",
        "up",
        "-d",
        "--build",
        "postgres",
        "redis",
        "api",
        "worker",
        "bot-runtime",
    )
    print("[GHBF] Waiting for dependency readiness…")
    wait_for_api(port)

    quoted_code = urllib.parse.quote(setup_code, safe="")
    print("\nGH Bot Factory is ready for first-run setup.")
    print(f"Open: http://127.0.0.1:{port}/setup/?code={quoted_code}")
    print("The setup code is intentionally shown only in this local terminal output.")
    print("After setup, the installer locks itself and bot credentials live in the encrypted local vault.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"Command failed with exit code {exc.returncode}.") from exc
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
