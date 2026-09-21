from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from packages.core.config import settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if settings.is_production_like:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.url.path.startswith("/miniapp"):
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: https:; connect-src 'self' https:; frame-ancestors 'self' https://web.telegram.org https://*.telegram.org",
            )
            response.headers.setdefault("Cache-Control", "no-cache, no-store, must-revalidate")
            response.headers.setdefault("Pragma", "no-cache")
        elif request.url.path.startswith("/admin"):
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: https:; connect-src 'self'; frame-ancestors 'self' https://web.telegram.org https://*.telegram.org",
            )
            response.headers.setdefault("Cache-Control", "no-cache, no-store, must-revalidate")
            response.headers.setdefault("Pragma", "no-cache")
        elif request.url.path.startswith("/setup"):
            response.headers.setdefault("Cache-Control", "no-cache, no-store, must-revalidate")
            response.headers.setdefault("Pragma", "no-cache")
        elif request.url.path.startswith("/build"):
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: https:; connect-src 'self'; frame-ancestors 'self'",
            )
            response.headers.setdefault("Cache-Control", "no-cache, no-store, must-revalidate")
            response.headers.setdefault("Pragma", "no-cache")
        return response


class MaxBodySizeMiddleware:
    def __init__(self, app, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        raw_length = headers.get(b"content-length")
        if raw_length:
            try:
                if int(raw_length) > self.max_bytes:
                    response = JSONResponse({"detail": "Request body too large"}, status_code=413)
                    await response(scope, receive, send)
                    return
            except ValueError:
                pass

        if scope.get("method") not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.max_bytes:
                response = JSONResponse({"detail": "Request body too large"}, status_code=413)
                await response(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break

        body = b"".join(chunks)
        delivered = False

        async def replay_receive():
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay_receive, send)


@dataclass(frozen=True)
class RatePolicy:
    name: str
    limit: int


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app) -> None:
        super().__init__(app)
        self._local: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    def _policy(self, request: Request) -> RatePolicy | None:
        if not settings.rate_limit_enabled:
            return None
        path = request.url.path
        if path in {"/api/v1/auth/telegram-miniapp", "/api/v1/auth/admin-code", "/api/v1/auth/login", "/api/v1/auth/account/password"}:
            return RatePolicy("auth", settings.rate_limit_auth_per_minute)
        if request.method == "POST" and path in {
            "/api/v1/storefront/checkout",
            "/api/v1/storefront/wallet/topups",
            "/api/v1/storefront/wallet/topups/method",
            "/api/v1/storefront/wallet/topups/local",
            "/api/v1/storefront/wallet/flexible-deposits",
        }:
            return RatePolicy("money", settings.rate_limit_money_per_minute)
        if (
            request.method == "POST"
            and path.startswith("/api/v1/payments")
            and "/webhooks/" not in path
        ):
            return RatePolicy("money", settings.rate_limit_money_per_minute)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and path.startswith(
            ("/api/v1/admin", "/api/v1/platform")
        ):
            return RatePolicy("admin_write", settings.rate_limit_admin_write_per_minute)
        return None

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        policy = self._policy(request)
        if policy is None:
            return await call_next(request)
        client_host = request.client.host if request.client else "unknown"
        key = f"ratelimit:{policy.name}:{client_host}"
        allowed = await self._allow(key, policy.limit)
        if allowed is None:
            return JSONResponse(
                {"detail": "Rate limit service unavailable"},
                status_code=503,
                headers={"Retry-After": "5"},
            )
        if not allowed:
            return JSONResponse(
                {"detail": "Too many requests"},
                status_code=429,
                headers={"Retry-After": "60"},
            )
        return await call_next(request)

    async def _allow(self, key: str, limit: int) -> bool | None:
        if settings.rate_limit_backend == "redis":
            from redis.asyncio import Redis

            client = Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
            try:
                value = await client.incr(key)
                if value == 1:
                    await client.expire(key, 60)
                return int(value) <= limit
            except Exception:  # noqa: BLE001 - backend errors fail closed at the request boundary
                if settings.is_production_like:
                    return None
                return True
            finally:
                await client.aclose()

        now = time.monotonic()
        cutoff = now - 60
        async with self._lock:
            bucket = self._local[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True
