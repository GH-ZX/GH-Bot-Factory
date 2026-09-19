from __future__ import annotations

import asyncio
import json
import uuid
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

DEFAULT_G2BULK_BASE_URL = "https://api.g2bulk.com/v1"


class G2BulkClient(BaseProviderClient):
    """Native integration client for G2Bulk API (api.g2bulk.com/v1).

    Wholesale digital goods, vouchers, game top-ups (PUBG Mobile, Free Fire, MLBB,
    Roblox, Razer Gold), and digital apps.
    Documentation: https://api.g2bulk.com/docs
    """

    def __init__(
        self,
        provider_name: str = "G2Bulk",
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(provider_name=provider_name, config=config)
        raw_url = str(self.config.get("base_url") or DEFAULT_G2BULK_BASE_URL).strip()
        self.base_url = validate_http_base_url(raw_url)
        credentials = self.config.get("credentials") or {}
        api_key = credentials.get("API_KEY") or self.config.get("api_key")
        if not api_key:
            raise ProviderConfigurationError("G2Bulk requires an 'API_KEY' (X-API-Key) credential.")
        self.api_key = str(api_key).strip()
        self.timeout_seconds = float(self.config.get("timeout_seconds") or 15.0)

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {
            "User-Agent": "GH-Bot-Factory/1.0",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-Key": self.api_key,
        }
        if idempotency_key:
            headers["X-Idempotency-Key"] = self._format_uuid_idempotency_key(idempotency_key)
        return headers

    @staticmethod
    def _format_uuid_idempotency_key(key: str) -> str:
        """Convert any arbitrary idempotency string into a valid RFC 4122 36-char UUID.

        G2Bulk strictly requires X-Idempotency-Key to be a 36-character UUID.
        """
        raw = key.strip()
        try:
            return str(uuid.UUID(raw))
        except ValueError:
            return str(uuid.uuid5(uuid.NAMESPACE_DNS, raw))

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        hostname = urlsplit(self.base_url).hostname
        assert hostname is not None
        await assert_resolved_host_safe(hostname)

        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        timeout = httpx.Timeout(self.timeout_seconds)
        transport = self.config.get("transport")

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
                transport=transport,
            ) as client, client.stream(
                method.upper(),
                url,
                headers=self._headers(idempotency_key=idempotency_key),
                params=params,
                json=json_data,
            ) as response:
                if response.status_code == 401:
                    raise ProviderAuthenticationError("Invalid or missing G2Bulk API key (X-API-Key).")
                if response.status_code == 402:
                    raise ProviderInsufficientBalanceError("G2Bulk reported insufficient wallet balance.")
                if response.status_code == 404:
                    raise ProviderProductUnavailableError("G2Bulk resource was not found.")
                if response.status_code == 410:
                    # G2Bulk delivery polling 410 = Terminal failure / auto-refunded
                    body = await response.aread()
                    try:
                        return response.json()
                    except (json.JSONDecodeError, ValueError):
                        return {"success": False, "status": "REFUNDED", "message": "Order failed and was refunded automatically."}
                if response.status_code == 429:
                    raise ProviderRateLimitError("G2Bulk rate limit exceeded.")
                if response.status_code in {408, 504}:
                    raise ProviderTimeoutError("G2Bulk request timed out upstream.")
                if response.status_code >= 500:
                    raise ProviderError("G2Bulk upstream server error.", is_retryable=True)
                if response.status_code >= 400:
                    raw_body = (await response.aread()).decode(errors="replace")
                    raise ProviderOrderFailedError(
                        f"G2Bulk rejected request ({response.status_code}): {raw_body[:200]}",
                        is_retryable=False,
                    )

                body = await response.aread()
                if not body:
                    return {}
                return response.json()
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("G2Bulk connection timed out.") from exc
        except httpx.RequestError as exc:
            raise ProviderError("G2Bulk network request failed.", is_retryable=True) from exc

    async def health_check(self) -> ProviderHealthResult:
        started = asyncio.get_running_loop().time()
        try:
            data = await self._request("GET", "/getMe")
            elapsed = (asyncio.get_running_loop().time() - started) * 1000
            user = data.get("username") or str(data.get("user_id") or "user")
            bal = data.get("balance", "0.00")
            return ProviderHealthResult(
                status=ProviderHealthStatus.HEALTHY,
                latency_ms=elapsed,
                message=f"Connected as {user} (Balance: ${bal} USD).",
            )
        except (ProviderError, httpx.HTTPError, OSError, ValueError) as exc:
            elapsed = (asyncio.get_running_loop().time() - started) * 1000
            return ProviderHealthResult(
                status=ProviderHealthStatus.UNAVAILABLE,
                latency_ms=elapsed,
                message=str(exc)[:500],
            )

    async def get_balance(self) -> ProviderBalanceResult:
        data = await self._request("GET", "/getMe")
        raw_balance = data.get("balance", 0.0)
        return ProviderBalanceResult(
            balance=Decimal(str(raw_balance)),
            currency="USD",
        )

    async def list_products(self) -> list[ProviderProductDTO]:
        data = await self._request("GET", "/products")
        raw_products = data.get("products")
        if not isinstance(raw_products, list):
            raise ProviderError("G2Bulk catalog did not return a products list.")

        results: list[ProviderProductDTO] = []
        for item in raw_products:
            if not isinstance(item, dict):
                continue
            pid = str(item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            cat = str(item.get("category_title") or "").strip()
            name = f"{cat} - {title}" if cat and cat != title else title
            if not pid or not name:
                continue
            cost = Decimal(str(item.get("unit_price") or "0.00"))
            stock = item.get("stock")
            desc = str(item.get("description") or "").strip()
            results.append(
                ProviderProductDTO(
                    external_id=pid,
                    name=name,
                    cost=cost,
                    currency="USD",
                    is_available=bool(stock is None or stock > 0),
                    stock=int(stock) if isinstance(stock, int) else None,
                    description=desc,
                )
            )
        return results

    async def get_product(self, external_id: str) -> ProviderProductDTO:
        data = await self._request("GET", f"/products/{external_id}")
        product = data.get("product") if isinstance(data.get("product"), dict) else data
        if not product or not isinstance(product, dict) or not product.get("id"):
            raise ProviderProductUnavailableError(f"G2Bulk product '{external_id}' not found.")

        title = str(product.get("title") or "").strip()
        cat = str(product.get("category_title") or "").strip()
        name = f"{cat} - {title}" if cat and cat != title else title
        cost = Decimal(str(product.get("unit_price") or "0.00"))
        stock = product.get("stock")
        desc = str(product.get("description") or "").strip()

        return ProviderProductDTO(
            external_id=str(product["id"]),
            name=name,
            cost=cost,
            currency="USD",
            is_available=bool(stock is None or stock > 0),
            stock=int(stock) if isinstance(stock, int) else None,
            description=desc,
        )

    def _extract_delivery_artifacts(self, data: dict[str, Any]) -> tuple[ProviderDeliveryArtifact, ...]:
        artifacts: list[ProviderDeliveryArtifact] = []
        delivery_items = data.get("delivery_items")
        if isinstance(delivery_items, list):
            for item in delivery_items:
                val = str(item).strip()
                if val:
                    artifacts.append(
                        ProviderDeliveryArtifact(
                            kind=ProviderDeliveryKind.CODE,
                            value=val,
                            fields={"raw_item": item},
                        )
                    )
        return tuple(artifacts)

    async def create_order(self, request: ProviderOrderRequest) -> ProviderOrderResponse:
        pid = request.external_product_id.strip()
        body: dict[str, Any] = {
            "quantity": request.quantity or 1,
        }

        data = await self._request(
            "POST",
            f"/products/{pid}/purchase",
            json_data=body,
            idempotency_key=request.idempotency_key,
        )

        ext_order_id = str(data.get("order_id") or data.get("id") or "").strip()
        if not ext_order_id:
            raise ProviderError("G2Bulk purchase response did not include order_id.")

        raw_status = str(data.get("status") or "PENDING").upper()
        canonical = self._normalize_status(raw_status)
        artifacts = self._extract_delivery_artifacts(data)
        cost = Decimal(str(data.get("total_price") or data.get("unit_price") or "0.00"))

        return ProviderOrderResponse(
            external_order_id=ext_order_id,
            status=canonical.value,
            cost=cost,
            is_success=canonical != ProviderOrderState.FAILED,
            canonical_state=canonical,
            delivery=artifacts,
            raw_data=data,
        )

    async def get_order(self, external_order_id: str) -> ProviderOrderCheckResponse:
        try:
            data = await self._request("GET", f"/orders/{external_order_id}/delivery")
        except ProviderProductUnavailableError:
            # Fall back to main order status endpoint
            data = await self._request("GET", f"/orders/{external_order_id}")

        raw_status = str(data.get("status") or "PROCESSING").upper()
        canonical = self._normalize_status(raw_status)
        artifacts = self._extract_delivery_artifacts(data)

        return ProviderOrderCheckResponse(
            external_order_id=str(data.get("order_id") or external_order_id),
            status=canonical.value,
            is_completed=canonical == ProviderOrderState.COMPLETED,
            is_failed=canonical == ProviderOrderState.FAILED,
            canonical_state=canonical,
            delivery=artifacts,
            raw_data=data,
        )

    @staticmethod
    def _normalize_status(raw_status: str) -> ProviderOrderState:
        s = raw_status.strip().upper()
        if s in {"COMPLETED", "SUCCESS", "DELIVERED", "DONE"}:
            return ProviderOrderState.COMPLETED
        if s in {"PENDING", "PROCESSING", "ACCEPTED"}:
            return ProviderOrderState.PROCESSING
        if s in {"REFUNDED", "FAILED", "CANCELLED", "CANCELED"}:
            return ProviderOrderState.FAILED
        return normalize_provider_order_state(s)

    # Game top-up direct helpers
    async def list_games(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/games")
        return list(data.get("games") or [])

    async def list_game_catalogue(self, game_code: str) -> list[dict[str, Any]]:
        data = await self._request("GET", f"/games/{game_code}/catalogue")
        return list(data.get("catalogues") or [])

    async def validate_player_id(
        self,
        game_code: str,
        player_id: str,
        server_id: str | None = None,
        charname: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"game": game_code, "user_id": player_id}
        if server_id:
            body["server_id"] = server_id
        if charname:
            body["charname"] = charname
        return await self._request("POST", "/games/checkPlayerId", json_data=body)
