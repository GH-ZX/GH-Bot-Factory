from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

import httpx

from packages.providers.clients.base import BaseProviderClient
from packages.providers.contracts import (
    ProviderDeliveryArtifact,
    ProviderDeliveryKind,
    ProviderOrderState,
    normalize_provider_order_state,
)
from packages.providers.exceptions import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderError,
    ProviderInsufficientBalanceError,
    ProviderOrderFailedError,
    ProviderProductUnavailableError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from packages.providers.http_generic import assert_resolved_host_safe, validate_http_base_url
from packages.providers.interface import (
    ProviderBalanceResult,
    ProviderHealthResult,
    ProviderOrderCheckResponse,
    ProviderOrderRequest,
    ProviderOrderResponse,
    ProviderProductDTO,
)
from packages.providers.models import ProviderHealthStatus

DEFAULT_VENTEBOT_BASE_URL = "https://ventetelegrambotrailway-production.up.railway.app"


class VenteBotClient(BaseProviderClient):
    """Native integration client for VenteBot Reseller API.

    API Documentation: https://ventetelegrambotrailway-production.up.railway.app/api/swagger/
    OpenAPI Specification: https://ventetelegrambotrailway-production.up.railway.app/api/reseller/openapi.json
    """

    def __init__(
        self,
        provider_name: str = "VenteBot",
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(provider_name=provider_name, config=config)
        raw_url = str(self.config.get("base_url") or DEFAULT_VENTEBOT_BASE_URL).strip()
        self.base_url = validate_http_base_url(raw_url)
        credentials = self.config.get("credentials") or {}
        api_key = credentials.get("API_KEY") or self.config.get("api_key")
        if not api_key:
            raise ProviderConfigurationError("VenteBot requires an 'API_KEY' credential.")
        self.api_key = str(api_key).strip()
        self.timeout_seconds = float(self.config.get("timeout_seconds") or 15.0)

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": "GH-Bot-Factory/1.0",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Reseller-Key": self.api_key,
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        hostname = urlsplit(self.base_url).hostname
        assert hostname is not None
        await assert_resolved_host_safe(hostname)

        url = f"{self.base_url}{path}"
        timeout = httpx.Timeout(self.timeout_seconds)

        try:
            transport = self.config.get("transport")
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
                transport=transport,
            ) as client, client.stream(
                method.upper(),
                url,
                headers=self._headers(),
                params=params,
                json=json_data,
            ) as response:
                if response.status_code == 401:
                    raise ProviderAuthenticationError("Invalid or missing VenteBot reseller API key.")
                if response.status_code == 402:
                    raise ProviderInsufficientBalanceError("VenteBot reported insufficient wallet balance.")
                if response.status_code == 403:
                    raise ProviderAuthenticationError("Calling IP is not allowlisted in VenteBot security settings.")
                if response.status_code == 404:
                    raise ProviderProductUnavailableError("VenteBot resource was not found.")
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    delay = f" Retry after {retry_after}s." if retry_after else ""
                    raise ProviderRateLimitError(f"VenteBot rate limit exceeded (60 req/min).{delay}")
                if response.status_code in {408, 504}:
                    raise ProviderTimeoutError("VenteBot request timed out upstream.")
                if response.status_code >= 500:
                    raise ProviderError("VenteBot upstream server error.", is_retryable=True)
                if response.status_code >= 400:
                    raw_body = (await response.aread()).decode(errors="replace")
                    raise ProviderOrderFailedError(
                        f"VenteBot rejected request ({response.status_code}): {raw_body[:200]}",
                        is_retryable=False,
                    )

                body = await response.aread()
                if not body:
                    return {}
                return response.json()
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("VenteBot connection timed out.") from exc
        except httpx.RequestError as exc:
            raise ProviderError("VenteBot network request failed.", is_retryable=True) from exc

    async def health_check(self) -> ProviderHealthResult:
        started = asyncio.get_running_loop().time()
        try:
            data = await self._request("GET", "/api/reseller/me")
            elapsed = (asyncio.get_running_loop().time() - started) * 1000
            user_handle = data.get("username") or str(data.get("user_telegram_id") or "")
            key_name = data.get("key_name") or "active"
            return ProviderHealthResult(
                status=ProviderHealthStatus.HEALTHY,
                latency_ms=elapsed,
                message=f"Connected as {user_handle} ({key_name}).",
            )
        except (ProviderError, httpx.HTTPError, OSError, ValueError) as exc:
            elapsed = (asyncio.get_running_loop().time() - started) * 1000
            return ProviderHealthResult(
                status=ProviderHealthStatus.UNHEALTHY,
                latency_ms=elapsed,
                message=str(exc)[:500],
            )

    async def get_balance(self) -> ProviderBalanceResult:
        data = await self._request("GET", "/api/reseller/me")
        raw_balance = data.get("wallet_balance", 0.0)
        return ProviderBalanceResult(
            balance=Decimal(str(raw_balance)),
            currency="USD",
        )

    async def list_products(self) -> list[ProviderProductDTO]:
        data = await self._request("GET", "/api/reseller/products")
        raw_products = data.get("products")
        if not isinstance(raw_products, list):
            raise ProviderError("VenteBot catalog did not return a products list.")

        results: list[ProviderProductDTO] = []
        for item in raw_products:
            if not isinstance(item, dict):
                continue
            pid = str(item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not pid or not name:
                continue
            cost = Decimal(str(item.get("price_usd") or "0.00"))
            stock = item.get("stock")
            results.append(
                ProviderProductDTO(
                    external_id=pid,
                    name=name,
                    cost=cost,
                    currency="USD",
                    is_available=True,
                    stock=int(stock) if isinstance(stock, int) else None,
                )
            )
        return results

    async def get_product(self, external_id: str) -> ProviderProductDTO:
        products = await self.list_products()
        for p in products:
            if p.external_id == str(external_id).strip():
                return p
        raise ProviderProductUnavailableError(f"VenteBot product '{external_id}' not found.")

    def _extract_delivery_artifacts(self, order_data: dict[str, Any]) -> tuple[ProviderDeliveryArtifact, ...]:
        artifacts: list[ProviderDeliveryArtifact] = []
        items = order_data.get("items") or []
        for item in items:
            if not isinstance(item, dict):
                continue
            account_data = item.get("account_data")
            if account_data:
                artifacts.append(
                    ProviderDeliveryArtifact(
                        kind=ProviderDeliveryKind.ACCOUNT,
                        value=str(account_data),
                        fields={"item_id": item.get("id")},
                    )
                )
        return tuple(artifacts)

    async def create_order(self, request: ProviderOrderRequest) -> ProviderOrderResponse:
        try:
            pid = int(request.external_product_id)
        except (ValueError, TypeError) as exc:
            raise ProviderConfigurationError(f"VenteBot product ID must be an integer: {request.external_product_id}") from exc

        body: dict[str, Any] = {
            "product_id": pid,
            "quantity": request.quantity or 1,
            "idempotency_key": request.idempotency_key,
        }
        if request.recipient:
            body["activation_identifier"] = str(request.recipient)[:500]

        data = await self._request("POST", "/api/reseller/orders", json_data=body)
        order = data.get("order") or {}
        ext_order_id = str(order.get("id") or "").strip()
        if not ext_order_id:
            raise ProviderError("VenteBot order creation response did not include order.id.")

        raw_status = str(order.get("status") or "PENDING").upper()
        canonical = self._normalize_status(raw_status)
        cost = Decimal(str(order.get("amount_usd") or data.get("total") or "0.00"))
        artifacts = self._extract_delivery_artifacts(order)

        return ProviderOrderResponse(
            external_order_id=ext_order_id,
            status=canonical.value,
            cost=cost,
            is_success=canonical != ProviderOrderState.FAILED,
            canonical_state=canonical,
            delivery=artifacts,
            raw_data={"order": order, "idempotent": data.get("idempotent", False)},
        )

    async def get_order(self, external_order_id: str) -> ProviderOrderCheckResponse:
        data = await self._request("GET", f"/api/reseller/orders/{external_order_id}")
        order = data.get("order") or {}
        raw_status = str(order.get("status") or "UNKNOWN").upper()
        canonical = self._normalize_status(raw_status)
        artifacts = self._extract_delivery_artifacts(order)

        return ProviderOrderCheckResponse(
            external_order_id=str(order.get("id") or external_order_id),
            status=canonical.value,
            is_completed=canonical == ProviderOrderState.COMPLETED,
            is_failed=canonical == ProviderOrderState.FAILED,
            canonical_state=canonical,
            delivery=artifacts,
            raw_data=order,
        )

    @staticmethod
    def _normalize_status(raw_status: str) -> ProviderOrderState:
        s = raw_status.strip().upper()
        if s in {"COMPLETED", "OK", "SUCCESS", "DONE"}:
            return ProviderOrderState.COMPLETED
        if s in {"PAID_PENDING_DELIVERY", "AWAITING_ACTIVATION", "AWAITING_ACTIVATION_INFO", "PROCESSING", "PENDING"}:
            return ProviderOrderState.PROCESSING
        if s in {"CANCELLED", "CANCELED", "FAILED", "REJECTED"}:
            return ProviderOrderState.FAILED
        return normalize_provider_order_state(s)
