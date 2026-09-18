from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

import httpx

from packages.providers.clients.base import BaseProviderClient
from packages.providers.contracts import (
    NumberActivationSnapshot,
    NumberActivationState,
    NumberCountryDTO,
    ProviderDeliveryArtifact,
    ProviderDeliveryKind,
    ProviderOrderState,
    SmsMessageDTO,
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

DEFAULT_SPIDER_BASE_URL = "https://api.spider-service.com"


class SpiderServiceClient(BaseProviderClient):
    """Native integration client for Spider Service (api.spider-service.com).

    Virtual Numbers and SMS Activation API for Telegram bots and resellers.
    Documentation: https://www.spider-service.com/
    """

    def __init__(
        self,
        provider_name: str = "SpiderService",
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(provider_name=provider_name, config=config)
        raw_url = str(self.config.get("base_url") or DEFAULT_SPIDER_BASE_URL).strip()
        self.base_url = validate_http_base_url(raw_url)
        credentials = self.config.get("credentials") or {}
        api_key = credentials.get("API_KEY") or self.config.get("api_key")
        if not api_key:
            raise ProviderConfigurationError("Spider Service requires an 'API_KEY' (apiKay) credential.")
        self.api_key = str(api_key).strip()
        self.timeout_seconds = float(self.config.get("timeout_seconds") or 15.0)

    async def _request(
        self,
        action: str,
        extra_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        hostname = urlsplit(self.base_url).hostname
        assert hostname is not None
        await assert_resolved_host_safe(hostname)

        params: dict[str, Any] = {
            "apiKay": self.api_key,
            "action": action,
        }
        if extra_params:
            for k, v in extra_params.items():
                if v is not None:
                    params[k] = str(v)

        url = f"{self.base_url}/"
        timeout = httpx.Timeout(self.timeout_seconds)
        transport = self.config.get("transport")

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
                transport=transport,
            ) as client, client.stream(
                "GET",
                url,
                headers={"User-Agent": "GH-Bot-Factory/1.0", "Accept": "application/json"},
                params=params,
            ) as response:
                if response.status_code == 401:
                    raise ProviderAuthenticationError("Spider Service rejected authentication.")
                if response.status_code == 429:
                    raise ProviderRateLimitError("Spider Service rate limit exceeded.")
                if response.status_code >= 500:
                    raise ProviderError("Spider Service upstream server error.", is_retryable=True)
                if response.status_code >= 400:
                    raise ProviderOrderFailedError(f"Spider Service HTTP error {response.status_code}.")

                body = await response.aread()
                if not body:
                    return {}
                data = response.json()
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Spider Service request timed out.") from exc
        except httpx.RequestError as exc:
            raise ProviderError("Spider Service network request failed.", is_retryable=True) from exc

        # Spider Service returns HTTP 200 with ok: false on business errors
        if isinstance(data, dict) and data.get("ok") is False:
            err = str(data.get("error") or data.get("msg") or "UNKNOWN_ERROR").strip()
            err_upper = err.upper()
            if err_upper in {"BAD_KEY", "NO_KEYS", "INVALID_KEY", "AUTH_ERROR"}:
                raise ProviderAuthenticationError(f"Invalid Spider Service API key ({err}).")
            if err_upper in {"NO_BALANCE", "LOW_BALANCE", "INSUFFICIENT_BALANCE"}:
                raise ProviderInsufficientBalanceError(f"Spider Service reported insufficient balance ({err}).")
            if err_upper in {"NO_NUMBER", "NO_NUMBERS", "NO_STOCK"}:
                raise ProviderProductUnavailableError(f"No numbers available in selected country ({err}).")
            if err_upper == "WAIT_CODE":
                return data  # Caller inspects WAIT_CODE for ongoing activation polling
            raise ProviderError(f"Spider Service returned error: {err}")

        return data

    async def health_check(self) -> ProviderHealthResult:
        started = asyncio.get_running_loop().time()
        try:
            await self._request("getBalance")
            elapsed = (asyncio.get_running_loop().time() - started) * 1000
            return ProviderHealthResult(
                status=ProviderHealthStatus.HEALTHY,
                latency_ms=elapsed,
                message="Connected to Spider Service.",
            )
        except (ProviderError, httpx.HTTPError, OSError, ValueError) as exc:
            elapsed = (asyncio.get_running_loop().time() - started) * 1000
            return ProviderHealthResult(
                status=ProviderHealthStatus.UNAVAILABLE,
                latency_ms=elapsed,
                message=str(exc)[:500],
            )

    async def get_balance(self) -> ProviderBalanceResult:
        data = await self._request("getBalance")
        result = data.get("result")
        raw_bal = 0.0
        if isinstance(result, dict):
            raw_bal = result.get("balance") or result.get("main_balance") or 0.0
        elif isinstance(result, (int, float, str)):
            raw_bal = result
        else:
            raw_bal = data.get("balance", 0.0)

        return ProviderBalanceResult(
            balance=Decimal(str(raw_bal)),
            currency="USD",
        )

    async def list_number_countries(self, service: str | None = None) -> list[NumberCountryDTO]:
        data = await self._request("getCountrys")
        countries = data.get("result") or data.get("countries") or []
        results: list[NumberCountryDTO] = []
        if isinstance(countries, list):
            for c in countries:
                if isinstance(c, dict):
                    code = str(c.get("country") or c.get("code") or "").strip().upper()
                    name = str(c.get("name") or code).strip()
                    dial = str(c.get("dial_code") or c.get("prefix") or "").strip() or None
                    if code:
                        results.append(NumberCountryDTO(code=code, name=name, dial_code=dial))
                elif isinstance(c, str) and c.strip():
                    code = c.strip().upper()
                    results.append(NumberCountryDTO(code=code, name=code))
        return results

    async def reserve_number(self, request: Any) -> NumberActivationSnapshot:
        country = (
            getattr(request, "country", None)
            or (request.parameters.get("country") if hasattr(request, "parameters") and request.parameters.get("country") else None)
            or getattr(request, "external_product_id", None)
            or "PS"
        )
        server = getattr(request, "server", None) or (request.parameters.get("server") if hasattr(request, "parameters") else None)
        params: dict[str, Any] = {"country": country}
        if server:
            params["server"] = server

        data = await self._request("getNumber", extra_params=params)
        result = data.get("result") or {}
        hash_code = str(result.get("hash_code") or result.get("id") or "").strip()
        if not hash_code:
            raise ProviderError("Spider Service did not return hash_code for number reservation.")

        phone_num = str(result.get("number") or result.get("phone") or "").strip() or None
        return NumberActivationSnapshot(
            external_order_id=hash_code,
            state=NumberActivationState.WAITING_SMS,
            phone_number=phone_num,
            currency="USD",
            raw_data=data,
        )

    async def get_number_activation(self, external_order_id: str) -> NumberActivationSnapshot:
        data = await self._request("getCode", extra_params={"hash_code": external_order_id})
        if data.get("ok") is False and str(data.get("error") or "").upper() == "WAIT_CODE":
            return NumberActivationSnapshot(
                external_order_id=external_order_id,
                state=NumberActivationState.WAITING_SMS,
                raw_data=data,
            )

        result = data.get("result") or {}
        code = None
        if isinstance(result, dict):
            code = str(result.get("code") or result.get("sms") or "").strip() or None
        elif isinstance(result, str):
            code = result.strip() or None

        if code:
            return NumberActivationSnapshot(
                external_order_id=external_order_id,
                state=NumberActivationState.SMS_RECEIVED,
                messages=(SmsMessageDTO(code=code, text=f"Activation code: {code}"),),
                raw_data=data,
            )

        return NumberActivationSnapshot(
            external_order_id=external_order_id,
            state=NumberActivationState.WAITING_SMS,
            raw_data=data,
        )

    # BaseProviderClient general catalog / order methods
    async def list_products(self) -> list[ProviderProductDTO]:
        countries = await self.list_number_countries()
        if not countries:
            return [
                ProviderProductDTO(
                    external_id="PS",
                    name="Spider Number - Palestine (PS)",
                    cost=Decimal("1.00"),
                    currency="USD",
                    is_available=True,
                    description="Virtual number reservation and SMS activation via Spider Service.",
                )
            ]
        return [
            ProviderProductDTO(
                external_id=c.code,
                name=f"Spider Number - {c.name} ({c.code})",
                cost=Decimal("1.00"),
                currency="USD",
                is_available=True,
                description=f"Virtual number reservation for {c.name}.",
            )
            for c in countries
        ]

    async def get_product(self, external_id: str) -> ProviderProductDTO:
        products = await self.list_products()
        for p in products:
            if p.external_id == str(external_id).strip().upper():
                return p
        return ProviderProductDTO(
            external_id=external_id,
            name=f"Spider Number - {external_id}",
            cost=Decimal("1.00"),
            currency="USD",
            is_available=True,
        )

    async def create_order(self, request: ProviderOrderRequest) -> ProviderOrderResponse:
        snapshot = await self.reserve_number(request)
        artifacts: tuple[ProviderDeliveryArtifact, ...] = ()
        if snapshot.phone_number:
            artifacts = (
                ProviderDeliveryArtifact(
                    kind=ProviderDeliveryKind.PHONE_NUMBER,
                    value=snapshot.phone_number,
                    fields={"hash_code": snapshot.external_order_id},
                ),
            )
        return ProviderOrderResponse(
            external_order_id=snapshot.external_order_id,
            status=ProviderOrderState.PROCESSING.value,
            cost=Decimal("1.00"),
            is_success=True,
            canonical_state=ProviderOrderState.PROCESSING,
            delivery=artifacts,
            raw_data=snapshot.raw_data,
        )

    async def get_order(self, external_order_id: str) -> ProviderOrderCheckResponse:
        snapshot = await self.get_number_activation(external_order_id)
        is_completed = snapshot.state == NumberActivationState.SMS_RECEIVED
        artifacts: list[ProviderDeliveryArtifact] = []
        for msg in snapshot.messages:
            if msg.code:
                artifacts.append(
                    ProviderDeliveryArtifact(
                        kind=ProviderDeliveryKind.CODE,
                        value=msg.code,
                        fields={"text": msg.text},
                    )
                )
        return ProviderOrderCheckResponse(
            external_order_id=external_order_id,
            status="COMPLETED" if is_completed else "WAITING_SMS",
            is_completed=is_completed,
            is_failed=snapshot.state == NumberActivationState.FAILED,
            canonical_state=ProviderOrderState.COMPLETED if is_completed else ProviderOrderState.PROCESSING,
            delivery=tuple(artifacts),
            raw_data=snapshot.raw_data,
        )
