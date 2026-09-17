from __future__ import annotations

import logging
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from apps.api.v1.admin import router as admin_router
from apps.api.v1.admin_analytics import router as admin_analytics_router
from apps.api.v1.admin_bots import router as admin_bots_router
from apps.api.v1.admin_economics import router as admin_economics_router
from apps.api.v1.admin_finance import router as admin_finance_router
from apps.api.v1.admin_members import router as admin_members_router
from apps.api.v1.admin_onboarding import router as admin_onboarding_router
from apps.api.v1.admin_payments import router as admin_payments_router
from apps.api.v1.admin_providers import router as admin_providers_router
from apps.api.v1.admin_saas import router as admin_saas_router
from apps.api.v1.auth import router as auth_router
from apps.api.v1.payments import router as payments_router
from apps.api.v1.platform import router as platform_router
from apps.api.v1.platform_sales import router as platform_sales_router
from apps.api.v1.public_marketplace import router as public_marketplace_router
from apps.api.v1.saas_billing import router as saas_billing_router
from apps.api.v1.setup import router as setup_router
from apps.api.v1.storefront import router as storefront_router
from packages.core.config import settings
from packages.core.health import readiness_report
from packages.core.heartbeat import ServiceHeartbeat
from packages.core.observability import (
    configure_logging,
    get_request_id,
    metrics,
    reset_request_id,
    set_request_id,
)
from packages.core.operational_metrics import render_operational_metrics
from packages.core.security import (
    MaxBodySizeMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)

configure_logging(service="api", level=settings.log_level, json_logs=settings.log_json)
logger = logging.getLogger("apps.api")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

@asynccontextmanager
async def lifespan(_: FastAPI):
    heartbeat = ServiceHeartbeat("api")
    await heartbeat.start()
    try:
        yield
    finally:
        await heartbeat.stop()


app = FastAPI(
    title="GH Bot Factory API",
    version="0.1.0",
    docs_url=None if settings.app_env == "production" else "/docs",
    redoc_url=None if settings.app_env == "production" else "/redoc",
    openapi_url=None if settings.app_env == "production" else "/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(MaxBodySizeMiddleware, max_bytes=settings.max_request_body_bytes)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)


@app.middleware("http")
async def request_observability(request: Request, call_next):
    incoming = request.headers.get("X-Request-ID", "").strip()
    request_id = incoming if REQUEST_ID_RE.fullmatch(incoming) else None
    token = set_request_id(request_id)
    effective_request_id = get_request_id() or ""
    started = time.perf_counter()
    metrics.inc("ghbf_http_requests_in_flight")
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled API request failure")
        metrics.inc("ghbf_http_requests_total", labels={"method": request.method, "route": "unhandled", "status": "500"})
        raise
    finally:
        metrics.inc("ghbf_http_requests_in_flight", -1)
        reset_request_id(token)

    elapsed = time.perf_counter() - started
    route = request.scope.get("route")
    route_path = getattr(route, "path", request.url.path)
    labels = {"method": request.method, "route": route_path, "status": str(response.status_code)}
    metrics.inc("ghbf_http_requests_total", labels=labels)
    metrics.inc("ghbf_http_request_duration_seconds_sum", elapsed, labels={"method": request.method, "route": route_path})
    response.headers["X-Request-ID"] = effective_request_id
    return response


@app.get("/health", include_in_schema=False)
@app.get("/health/live", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", include_in_schema=False)
async def readiness():
    ready, report = await readiness_report()
    return JSONResponse(report, status_code=200 if ready else 503)


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics() -> PlainTextResponse:
    text = metrics.render_prometheus() + await render_operational_metrics()
    return PlainTextResponse(text, media_type="text/plain; version=0.0.4")


app.include_router(payments_router, prefix="/api/v1")
app.include_router(platform_router, prefix="/api/v1")
app.include_router(platform_sales_router, prefix="/api/v1")
app.include_router(saas_billing_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.include_router(admin_analytics_router, prefix="/api/v1")
app.include_router(admin_bots_router, prefix="/api/v1")
app.include_router(admin_members_router, prefix="/api/v1")
app.include_router(admin_onboarding_router, prefix="/api/v1")
app.include_router(admin_payments_router, prefix="/api/v1")
app.include_router(admin_economics_router, prefix="/api/v1")
app.include_router(admin_finance_router, prefix="/api/v1")
app.include_router(admin_providers_router, prefix="/api/v1")
app.include_router(admin_saas_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(storefront_router, prefix="/api/v1")
app.include_router(setup_router, prefix="/api/v1")
app.include_router(public_marketplace_router, prefix="/api/v1")

MINIAPP_STATIC_DIR = Path(__file__).resolve().parents[1] / "miniapp" / "static"
app.mount(
    "/miniapp",
    StaticFiles(directory=MINIAPP_STATIC_DIR, html=True),
    name="telegram-miniapp",
)

ADMIN_STATIC_DIR = Path(__file__).resolve().parents[1] / "admin" / "static"
app.mount(
    "/admin",
    StaticFiles(directory=ADMIN_STATIC_DIR, html=True),
    name="tenant-admin",
)

SETUP_STATIC_DIR = Path(__file__).resolve().parents[1] / "setup" / "static"
app.mount(
    "/setup",
    StaticFiles(directory=SETUP_STATIC_DIR, html=True),
    name="first-run-setup",
)

CONFIGURATOR_STATIC_DIR = Path(__file__).resolve().parents[1] / "configurator" / "static"
CONFIGURATOR_STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    "/build",
    StaticFiles(directory=CONFIGURATOR_STATIC_DIR, html=True),
    name="public-configurator",
)
