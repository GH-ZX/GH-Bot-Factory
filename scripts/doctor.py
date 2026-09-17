#!/usr/bin/env python3
"""Safe local diagnostics for a GH Bot Factory installation.

The doctor never prints secret values. It checks host prerequisites, local configuration,
Compose validity, and (when running) the API/runtime state. Use --require-running as a
post-start gate on a laptop or VPS.
"""

from __future__ import annotations

import argparse
import shutil
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
EXPECTED_SERVICES = ("postgres", "redis", "api", "worker", "bot-runtime")


class Doctor:
    def __init__(self) -> None:
        self.failures = 0
        self.warnings = 0

    def pass_(self, message: str) -> None:
        print(f"[PASS] {message}")

    def warn(self, message: str) -> None:
        self.warnings += 1
        print(f"[WARN] {message}")

    def fail(self, message: str) -> None:
        self.failures += 1
        print(f"[FAIL] {message}")


def _run(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_PATH.exists():
        return values
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _check_python(doctor: Doctor) -> None:
    if sys.version_info >= (3, 12):  # noqa: UP036 - doctor also runs before environment setup
        doctor.pass_(f"Python {sys.version_info.major}.{sys.version_info.minor} satisfies >=3.12")
    else:
        doctor.fail("Python >=3.12 is required")


def _check_env(doctor: Doctor) -> dict[str, str]:
    if not ENV_PATH.exists():
        doctor.warn(".env does not exist yet; run: python3 scripts/easy_start.py")
        return {}
    doctor.pass_(".env exists")
    try:
        mode = stat.S_IMODE(ENV_PATH.stat().st_mode)
        if mode & 0o077:
            doctor.warn(f".env permissions are {oct(mode)}; prefer 0o600")
        else:
            doctor.pass_(f".env permissions are restrictive ({oct(mode)})")
    except OSError:
        doctor.warn("Could not inspect .env permissions")

    values = _env_values()
    required = ("POSTGRES_PASSWORD", "DATABASE_URL", "JWT_SECRET_KEY", "PLATFORM_ADMIN_TOKEN")
    for key in required:
        value = values.get(key, "")
        if not value or value == "CHANGE_ME":
            doctor.fail(f"{key} is not configured")
        else:
            doctor.pass_(f"{key} is configured")
    if values.get("LOCAL_SECRET_VAULT_ENABLED", "true").lower() != "true":
        doctor.warn("LOCAL_SECRET_VAULT_ENABLED is not true; verify your external secret-custody design")
    return values


def _check_docker(doctor: Doctor) -> bool:
    if shutil.which("docker") is None:
        doctor.fail("Docker is not installed or not on PATH")
        return False
    version = _run("docker", "--version")
    if version.returncode != 0:
        doctor.fail("Docker CLI is unavailable")
        return False
    doctor.pass_("Docker CLI is available")
    compose = _run("docker", "compose", "version")
    if compose.returncode != 0:
        doctor.fail("Docker Compose plugin is unavailable")
        return False
    doctor.pass_("Docker Compose plugin is available")
    config = _run("docker", "compose", "config", "--quiet")
    if config.returncode != 0:
        doctor.fail("docker compose config is invalid; inspect .env and docker-compose.yml")
        return False
    doctor.pass_("Docker Compose configuration is valid")
    return True


def _running_services() -> set[str]:
    result = _run("docker", "compose", "ps", "--services", "--status", "running")
    if result.returncode != 0:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _check_live(doctor: Doctor, values: dict[str, str], *, require_running: bool) -> None:
    running = _running_services()
    if not running:
        if require_running:
            doctor.fail("No Compose services are running")
        else:
            doctor.warn("Stack is not running; live checks skipped")
        return

    for service in EXPECTED_SERVICES:
        if service in running:
            doctor.pass_(f"service {service} is running")
        elif require_running:
            doctor.fail(f"service {service} is not running")
        else:
            doctor.warn(f"service {service} is not running")

    port = values.get("API_HOST_PORT") or "8010"
    bind_address = (values.get("API_BIND_ADDRESS") or "0.0.0.0").strip()
    readiness_host = (
        "127.0.0.1" if bind_address in {"0.0.0.0", "::", "[::]"} else bind_address
    )
    if ":" in readiness_host and not readiness_host.startswith("["):
        readiness_host = f"[{readiness_host}]"
    ready_url = f"http://{readiness_host}:{port}/health/ready"
    try:
        with urllib.request.urlopen(ready_url, timeout=4) as response:
            if response.status == 200:
                doctor.pass_(f"API readiness returned 200 ({ready_url})")
            else:
                doctor.fail(f"API readiness returned HTTP {response.status}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        doctor.fail(f"API readiness failed: {type(exc).__name__}")

    if "api" in running:
        heads = _run(
            "docker", "compose", "exec", "-T", "api", "python", "-m", "alembic", "heads", timeout=30
        )
        if heads.returncode == 0 and heads.stdout.strip():
            normalized = " ".join(heads.stdout.split())
            doctor.pass_(f"Alembic head visible from API container: {normalized}")
        else:
            doctor.warn("Could not read Alembic head from running API container")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a GH Bot Factory laptop/VPS installation safely.")
    parser.add_argument(
        "--require-running",
        action="store_true",
        help="fail unless the expected Compose services and API readiness are live",
    )
    args = parser.parse_args()

    doctor = Doctor()
    print("GH Bot Factory Doctor")
    print("=====================")
    _check_python(doctor)
    values = _check_env(doctor)
    docker_ok = _check_docker(doctor)
    if docker_ok:
        _check_live(doctor, values, require_running=args.require_running)

    print("---------------------")
    print(f"Result: {doctor.failures} failure(s), {doctor.warnings} warning(s)")
    if doctor.failures:
        print("Fix failures before treating this host as ready.")
        return 1
    if doctor.warnings:
        print("No blocking failures; review warnings before production use.")
    else:
        print("Host checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
