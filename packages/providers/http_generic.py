from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from packages.core.config import settings
from packages.providers.catalog import ProviderCapability
from packages.providers.clients.base import BaseProviderClient
from packages.providers.contracts import (
    NumberActivationSnapshot,
    NumberActivationState,
    NumberCountryDTO,
    NumberOfferDTO,
    NumberReservationRequest,
    NumberServiceDTO,
    ProviderDeliveryArtifact,
    ProviderDeliveryKind,
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
from packages.providers.interface import (
    ProviderBalanceResult,
    ProviderHealthResult,
    ProviderOrderCheckResponse,
    ProviderOrderRequest,
    ProviderOrderResponse,
    ProviderProductDTO,
)
from packages.providers.models import ProviderCategory, ProviderHealthStatus

_GENERIC_CONFIG_KEY = "http_adapter"
_BLOCKED_HEADER_NAMES = {"host", "content-length", "transfer-encoding", "connection"}
_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_MAX_OPERATIONS = 64

_OPERATION_CAPABILITIES: dict[str, ProviderCapability] = {
    "HEALTH": ProviderCapability.HEALTH,
    "BALANCE": ProviderCapability.BALANCE,
    "CATALOG": ProviderCapability.CATALOG,
    "PRODUCT_DETAIL": ProviderCapability.PRODUCT_DETAIL,
    "CREATE_ORDER": ProviderCapability.CREATE_ORDER,
    "ORDER_STATUS": ProviderCapability.ORDER_STATUS,
    "CANCEL_ORDER": ProviderCapability.CANCEL_ORDER,
    "NUMBER_SERVICES": ProviderCapability.NUMBER_SERVICES,
    "NUMBER_COUNTRIES": ProviderCapability.NUMBER_COUNTRIES,
    "NUMBER_OFFERS": ProviderCapability.NUMBER_OFFERS,
    "NUMBER_RESERVE": ProviderCapability.NUMBER_RESERVE,
    "NUMBER_ACTIVATION": ProviderCapability.NUMBER_ACTIVATION,
    "NUMBER_CANCEL": ProviderCapability.NUMBER_CANCEL,
    "NUMBER_FINISH": ProviderCapability.NUMBER_FINISH,
}


class HttpAuthBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=50)
    location: Literal["header", "query"]
    name: str = Field(min_length=1, max_length=100)
    prefix: str = Field(default="", max_length=50)

    @field_validator("source")
    @classmethod
    def normalize_source(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(char in normalized for char in "\r\n"):
            raise ValueError("auth binding name must be a single non-empty header/query name")
        if normalized.lower() in _BLOCKED_HEADER_NAMES:
            raise ValueError(f"auth binding cannot control '{normalized}'")
        return normalized


class HttpRequestBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=120)
    target: str = Field(min_length=1, max_length=120)
    required: bool = False

    @field_validator("source", "target")
    @classmethod
    def strip_binding(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(char in normalized for char in "\r\n"):
            raise ValueError("request bindings must be single-line names")
        return normalized


class HttpDeliveryMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ProviderDeliveryKind
    value: str | None = Field(default=None, max_length=240)
    fields: dict[str, str] = Field(default_factory=dict)

    @field_validator("fields")
    @classmethod
    def bounded_fields(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 32:
            raise ValueError("delivery field mappings are limited to 32 entries")
        return {str(key).strip(): str(item).strip() for key, item in value.items()}


class HttpResponseMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: str | None = Field(default=None, max_length=240)
    fields: dict[str, str] = Field(default_factory=dict)
    status_map: dict[str, str] = Field(default_factory=dict)
    delivery: list[HttpDeliveryMapping] = Field(default_factory=list)

    @field_validator("fields", "status_map")
    @classmethod
    def bounded_mapping(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 64:
            raise ValueError("response mappings are limited to 64 entries")
        return {str(key).strip(): str(item).strip() for key, item in value.items()}


class HttpOperationMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str
    path: str = Field(min_length=1, max_length=500)
    operation_id: str | None = Field(default=None, max_length=200)
    query: list[HttpRequestBinding] = Field(default_factory=list)
    json_body: list[HttpRequestBinding] = Field(default_factory=list)
    path_params: list[HttpRequestBinding] = Field(default_factory=list)
    constants_query: dict[str, str | int | float | bool] = Field(default_factory=dict)
    constants_json: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    response: HttpResponseMapping = Field(default_factory=HttpResponseMapping)

    @field_validator("method")
    @classmethod
    def normalize_method(cls, value: str) -> str:
        method = value.strip().upper()
        if method not in _ALLOWED_METHODS:
            raise ValueError(f"method must be one of {sorted(_ALLOWED_METHODS)}")
        return method

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        path = value.strip()
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("operation path must be a relative URL path without query/fragment")
        if not path.startswith("/"):
            raise ValueError("operation path must begin with '/'")
        if ".." in path.split("/"):
            raise ValueError("operation path cannot contain parent traversal")
        return path

    @model_validator(mode="after")
    def validate_bindings(self) -> HttpOperationMapping:
        if len(self.query) + len(self.json_body) + len(self.path_params) > 64:
            raise ValueError("operation request mappings are limited to 64 bindings")
        placeholders = {
            segment[1:-1]
            for segment in self.path.split("/")
            if segment.startswith("{") and segment.endswith("}") and len(segment) > 2
        }
        bound = {binding.target for binding in self.path_params}
        if placeholders != bound:
            missing = sorted(placeholders - bound)
            extra = sorted(bound - placeholders)
            raise ValueError(
                f"path parameter bindings must exactly match placeholders; missing={missing}, extra={extra}"
            )
        return self


class GenericHttpAdapterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(min_length=1, max_length=1000)
    auth: list[HttpAuthBinding] = Field(default_factory=list)
    operations: dict[str, HttpOperationMapping]
    timeout_seconds: float = Field(default=15.0, ge=1.0, le=60.0)
    max_response_bytes: int = Field(default=1_048_576, ge=1024, le=4_194_304)

    @field_validator("operations")
    @classmethod
    def normalize_operations(
        cls, value: dict[str, HttpOperationMapping]
    ) -> dict[str, HttpOperationMapping]:
        if not value:
            raise ValueError("at least one operation mapping is required")
        if len(value) > _MAX_OPERATIONS:
            raise ValueError(f"at most {_MAX_OPERATIONS} operation mappings are allowed")
        normalized: dict[str, HttpOperationMapping] = {}
        for key, mapping in value.items():
            operation = str(key).strip().upper()
            if operation not in _OPERATION_CAPABILITIES:
                raise ValueError(f"unsupported canonical operation '{operation}'")
            normalized[operation] = mapping
        return normalized

    @model_validator(mode="after")
    def validate_url_and_auth(self) -> GenericHttpAdapterConfig:
        self.base_url = validate_http_base_url(self.base_url)
        sources = {binding.source for binding in self.auth}
        if len(sources) != len(self.auth):
            raise ValueError("each credential source may be bound only once")
        return self

    def required_credential_sources(self) -> set[str]:
        return {binding.source for binding in self.auth}


@dataclass(frozen=True, slots=True)
class OpenApiOperationInfo:
    operation_id: str
    method: str
    path: str
    summary: str | None = None


@dataclass(frozen=True, slots=True)
class OpenApiInspectionResult:
    version: str
    title: str | None
    operations: tuple[OpenApiOperationInfo, ...]


def _host_allowed(host: str, patterns: tuple[str, ...]) -> bool:
    lowered = host.lower().rstrip(".")
    for pattern in patterns:
        candidate = pattern.lower().strip().rstrip(".")
        if not candidate:
            continue
        if candidate.startswith("*."):
            suffix = candidate[1:]
            if lowered.endswith(suffix) and lowered != suffix.lstrip("."):
                return True
        elif lowered == candidate:
            return True
    return False


def validate_http_base_url(value: str) -> str:
    raw = value.strip().rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base_url must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("base_url cannot contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("base_url cannot contain query parameters or fragments")
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} and not settings.provider_http_allow_private_networks:
        raise ValueError("private/localhost provider hosts are disabled by installation policy")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global and not settings.provider_http_allow_private_networks:
        raise ValueError("private, loopback, link-local, and reserved provider addresses are blocked")
    if settings.is_production_like:
        if parsed.scheme != "https":
            raise ValueError("production-like Generic HTTP providers require HTTPS")
        allowed = settings.provider_http_allowed_host_set
        if not allowed or not _host_allowed(host, allowed):
            raise ValueError(
                "provider host is not allowlisted by PROVIDER_HTTP_ALLOWED_HOSTS in production-like mode"
            )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


async def assert_resolved_host_safe(host: str) -> None:
    if settings.provider_http_allow_private_networks:
        return
    loop = asyncio.get_running_loop()
    try:
        results = await loop.run_in_executor(
            None,
            lambda: socket.getaddrinfo(host, None, type=socket.SOCK_STREAM),
        )
    except socket.gaierror as exc:
        raise ProviderError("Provider hostname could not be resolved.", is_retryable=True) from exc
    addresses = {item[4][0] for item in results if item[4]}
    if not addresses:
        raise ProviderError("Provider hostname did not resolve to an address.", is_retryable=True)
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ProviderConfigurationError("Provider DNS returned an invalid IP address.") from exc
        if not parsed.is_global:
            raise ProviderConfigurationError(
                "Provider DNS resolved to a private, loopback, link-local, or reserved address."
            )


def parse_generic_http_config(metadata: dict[str, Any]) -> GenericHttpAdapterConfig:
    raw = metadata.get(_GENERIC_CONFIG_KEY)
    if not isinstance(raw, dict):
        raise ProviderConfigurationError(
            f"Generic HTTP adapter requires metadata.{_GENERIC_CONFIG_KEY}."
        )
    try:
        return GenericHttpAdapterConfig.model_validate(raw)
    except (ValidationError, ValueError) as exc:
        raise ProviderConfigurationError(f"Invalid Generic HTTP adapter configuration: {exc}") from exc


def validate_generic_http_metadata(metadata: dict[str, Any], category: ProviderCategory) -> None:
    config = parse_generic_http_config(metadata)
    operations = set(config.operations)
    number_ops = {key for key in operations if key.startswith("NUMBER_")}
    if number_ops and category != ProviderCategory.NUMBER:
        raise ProviderConfigurationError(
            "NUMBER_* operations may only be configured on NUMBER provider connections."
        )
    if category == ProviderCategory.NUMBER and number_ops and "NUMBER_ACTIVATION" not in operations:
        raise ProviderConfigurationError(
            "NUMBER providers with specialized operations must map NUMBER_ACTIVATION."
        )
    unknown_sources = config.required_credential_sources() - {
        "API_KEY",
        "API_SECRET",
        "BEARER_TOKEN",
        "USERNAME",
        "PASSWORD",
    }
    if unknown_sources:
        raise ProviderConfigurationError(
            f"Unsupported Generic HTTP credential source(s): {', '.join(sorted(unknown_sources))}."
        )


def generic_http_required_credentials(metadata: dict[str, Any]) -> set[str]:
    return parse_generic_http_config(metadata).required_credential_sources()


def generic_http_capabilities(
    metadata: dict[str, Any],
    category: ProviderCategory,
) -> tuple[ProviderCapability, ...]:
    del category
    config = parse_generic_http_config(metadata)
    values = [_OPERATION_CAPABILITIES[key] for key in config.operations]
    if "ORDER_STATUS" in config.operations or "NUMBER_ACTIVATION" in config.operations:
        values.append(ProviderCapability.POLLING)
    # Health can use a bounded authenticated read probe even when a dedicated endpoint is absent.
    if ProviderCapability.HEALTH not in values and any(
        capability in values
        for capability in (ProviderCapability.BALANCE, ProviderCapability.CATALOG)
    ):
        values.insert(0, ProviderCapability.HEALTH)
    return tuple(dict.fromkeys(values))


def inspect_openapi_document(document: dict[str, Any]) -> OpenApiInspectionResult:
    """Inspect OpenAPI/Swagger metadata without resolving refs or executing generated code."""
    if not isinstance(document, dict):
        raise ProviderConfigurationError("OpenAPI document must be a JSON object.")
    if document.get("openapi"):
        version = str(document["openapi"])
        if not version.startswith("3."):
            raise ProviderConfigurationError("Only OpenAPI 3.x documents are supported.")
    elif document.get("swagger"):
        version = str(document["swagger"])
        if version != "2.0":
            raise ProviderConfigurationError("Only Swagger 2.0 documents are supported.")
    else:
        raise ProviderConfigurationError("Document is neither OpenAPI 3.x nor Swagger 2.0.")

    paths = document.get("paths")
    if not isinstance(paths, dict):
        raise ProviderConfigurationError("OpenAPI document must contain a paths object.")
    operations: list[OpenApiOperationInfo] = []
    for path, path_item in paths.items():
        if not isinstance(path, str) or not isinstance(path_item, dict):
            continue
        if "$ref" in path_item:
            raise ProviderConfigurationError("External/path-level $ref resolution is intentionally unsupported.")
        for method in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            if "$ref" in operation:
                raise ProviderConfigurationError("Operation-level $ref resolution is intentionally unsupported.")
            operation_id = str(operation.get("operationId") or "").strip()
            if not operation_id:
                operation_id = f"{method.upper()} {path}"
            operations.append(
                OpenApiOperationInfo(
                    operation_id=operation_id[:200],
                    method=method.upper(),
                    path=path,
                    summary=str(operation.get("summary") or "").strip()[:500] or None,
                )
            )
            if len(operations) > 500:
                raise ProviderConfigurationError("OpenAPI operation inventory exceeds the 500-operation limit.")
    title = None
    info = document.get("info")
    if isinstance(info, dict) and info.get("title"):
        title = str(info["title"]).strip()[:200] or None
    return OpenApiInspectionResult(version=version, title=title, operations=tuple(operations))


def _select(data: Any, path: str | None, *, default: Any = None) -> Any:
    if path is None:
        return default
    if path == "":
        return data
    current = data
    for segment in path.split("."):
        if isinstance(current, dict):
            if segment not in current:
                return default
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit():
            index = int(segment)
            if index < 0 or index >= len(current):
                return default
            current = current[index]
        else:
            return default
    return current


def _source_value(source: str, context: dict[str, Any]) -> Any:
    return _select(context, source, default=None)


def _decimal(value: Any, *, default: Decimal = Decimal("0.00")) -> Decimal:
    if value in (None, ""):
        return default
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProviderError("Provider returned an invalid decimal value.") from exc
    if not result.is_finite():
        raise ProviderError("Provider returned a non-finite decimal value.")
    return result


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ProviderError("Provider returned an invalid integer value.") from exc


def _datetime_or_none(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    try:
        normalized = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError) as exc:
        raise ProviderError("Provider returned an invalid datetime value.") from exc


def _string(value: Any, *, default: str = "") -> str:
    return default if value is None else str(value)


def _redact_secret_values(value: Any, secrets: list[str] | tuple[str, ...] | set[str]) -> Any:
    """Return a JSON-compatible payload with exact/embedded credential values redacted."""
    secret_values = tuple(secret for secret in secrets if secret)
    if not secret_values:
        return value
    if isinstance(value, dict):
        return {
            key: _redact_secret_values(item, secret_values)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_secret_values(item, secret_values) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_secret_values(item, secret_values) for item in value)
    if isinstance(value, str):
        redacted = value
        for secret in secret_values:
            if secret in redacted:
                redacted = redacted.replace(secret, "[REDACTED]")
        return redacted
    return value


def _delivery_artifacts(
    root: Any,
    mapping: HttpResponseMapping,
) -> tuple[ProviderDeliveryArtifact, ...]:
    artifacts: list[ProviderDeliveryArtifact] = []
    for item in mapping.delivery:
        value = _select(root, item.value, default=None) if item.value is not None else None
        fields = {
            key: _select(root, selector, default=None)
            for key, selector in item.fields.items()
        }
        if value is None and not any(field_value is not None for field_value in fields.values()):
            continue
        artifacts.append(
            ProviderDeliveryArtifact(
                kind=item.kind,
                value=None if value is None else str(value),
                fields=fields,
            )
        )
    return tuple(artifacts)


class GenericHttpProvider(BaseProviderClient):
    """Constrained data-driven REST adapter.

    It intentionally supports only declarative mappings. It never evaluates provider-supplied
    Python, templates, expressions, shell commands, external refs, or browser-authored code.
    """

    def __init__(self, provider_name: str, config: dict[str, Any] | None = None) -> None:
        super().__init__(provider_name=provider_name, config=config)
        self.mapping = parse_generic_http_config(self.config)
        self.credentials = {
            str(key).strip().upper(): str(value)
            for key, value in (self.config.get("credentials") or {}).items()
        }
        missing = self.mapping.required_credential_sources() - set(self.credentials)
        if missing:
            raise ProviderConfigurationError(
                f"Missing Generic HTTP credential source(s): {', '.join(sorted(missing))}."
            )

    def _operation(self, key: str) -> HttpOperationMapping:
        operation = self.mapping.operations.get(key)
        if operation is None:
            raise ProviderConfigurationError(f"Generic HTTP operation '{key}' is not configured.")
        return operation

    async def _request_json(
        self,
        operation_key: str,
        context: dict[str, Any] | None = None,
    ) -> tuple[Any, HttpOperationMapping]:
        operation = self._operation(operation_key)
        context = context or {}
        parsed_base = urlsplit(self.mapping.base_url)
        assert parsed_base.hostname is not None
        await assert_resolved_host_safe(parsed_base.hostname)

        path = operation.path
        for binding in operation.path_params:
            value = _source_value(binding.source, context)
            if value is None:
                if binding.required:
                    raise ProviderConfigurationError(
                        f"Required request value '{binding.source}' is missing for {operation_key}."
                    )
                value = ""
            path = path.replace("{" + binding.target + "}", quote(str(value), safe=""))

        query: dict[str, Any] = dict(operation.constants_query)
        body: dict[str, Any] = dict(operation.constants_json)
        for binding in operation.query:
            value = _source_value(binding.source, context)
            if value is None:
                if binding.required:
                    raise ProviderConfigurationError(
                        f"Required request value '{binding.source}' is missing for {operation_key}."
                    )
                continue
            query[binding.target] = value
        for binding in operation.json_body:
            value = _source_value(binding.source, context)
            if value is None:
                if binding.required:
                    raise ProviderConfigurationError(
                        f"Required request value '{binding.source}' is missing for {operation_key}."
                    )
                continue
            body[binding.target] = value

        headers = {"Accept": "application/json"}
        for auth in self.mapping.auth:
            secret = self.credentials.get(auth.source)
            if secret is None:
                raise ProviderConfigurationError(
                    f"Credential source '{auth.source}' is not configured."
                )
            value = f"{auth.prefix}{secret}"
            if auth.location == "header":
                headers[auth.name] = value
            else:
                query[auth.name] = value

        url = f"{self.mapping.base_url}{path}"
        timeout = httpx.Timeout(self.mapping.timeout_seconds)
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client, client.stream(
                operation.method,
                url,
                params=query or None,
                json=body or None,
                headers=headers,
            ) as response:
                if 300 <= response.status_code < 400:
                    raise ProviderConfigurationError(
                        "Generic HTTP adapters do not follow upstream redirects."
                    )
                payload = await self._bounded_body(response)
                self._raise_for_status(response.status_code)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Generic HTTP provider request timed out.") from exc
        except httpx.RequestError as exc:
            raise ProviderError("Generic HTTP provider network request failed.", is_retryable=True) from exc

        if not payload:
            return {}, operation
        try:
            return json.loads(payload), operation
        except json.JSONDecodeError as exc:
            raise ProviderError("Generic HTTP provider returned invalid JSON.") from exc

    async def _bounded_body(self, response: httpx.Response) -> bytes:
        declared = response.headers.get("content-length")
        if declared:
            try:
                if int(declared) > self.mapping.max_response_bytes:
                    raise ProviderError("Provider response exceeds configured response-size limit.")
            except ValueError:
                pass
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > self.mapping.max_response_bytes:
                raise ProviderError("Provider response exceeds configured response-size limit.")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _raise_for_status(status_code: int) -> None:
        if 200 <= status_code < 300:
            return
        if status_code in {401, 403}:
            raise ProviderAuthenticationError("Provider authentication was rejected.")
        if status_code == 402:
            raise ProviderInsufficientBalanceError("Provider reported insufficient balance.")
        if status_code == 404:
            raise ProviderProductUnavailableError("Provider resource was not found or unavailable.")
        if status_code == 429:
            raise ProviderRateLimitError("Provider rate limit was exceeded.")
        if status_code in {408, 504}:
            raise ProviderTimeoutError("Provider request timed out upstream.")
        if 500 <= status_code < 600:
            raise ProviderError("Provider server error.", is_retryable=True)
        raise ProviderOrderFailedError("Provider rejected the request.", is_retryable=False)

    @staticmethod
    def _mapped_state(raw: Any, mapping: HttpResponseMapping) -> str:
        raw_text = _string(raw).strip()
        return mapping.status_map.get(raw_text, mapping.status_map.get(raw_text.upper(), raw_text))

    async def health_check(self) -> ProviderHealthResult:
        probe = next(
            (
                key
                for key in ("HEALTH", "BALANCE", "CATALOG")
                if key in self.mapping.operations
            ),
            None,
        )
        if probe is None:
            raise ProviderConfigurationError(
                "Generic HTTP adapter requires HEALTH, BALANCE, or CATALOG for connection testing."
            )
        started = asyncio.get_running_loop().time()
        data, operation = await self._request_json(probe)
        elapsed = (asyncio.get_running_loop().time() - started) * 1000
        status = ProviderHealthStatus.HEALTHY
        message = "Generic HTTP provider responded successfully."
        if probe == "HEALTH":
            fields = operation.response.fields
            raw_status = _select(data, fields.get("status"), default="HEALTHY")
            normalized = self._mapped_state(raw_status, operation.response).upper()
            if normalized in ProviderHealthStatus.__members__:
                status = ProviderHealthStatus[normalized]
            raw_message = _select(data, fields.get("message"), default=message)
            message = _string(raw_message, default=message)[:500]
        return ProviderHealthResult(status=status, latency_ms=elapsed, message=message)

    async def get_balance(self) -> ProviderBalanceResult:
        data, operation = await self._request_json("BALANCE")
        fields = operation.response.fields
        return ProviderBalanceResult(
            balance=_decimal(_select(data, fields.get("balance"))),
            currency=_string(_select(data, fields.get("currency"), default="USD"), default="USD").upper(),
        )

    async def list_products(self) -> list[ProviderProductDTO]:
        data, operation = await self._request_json("CATALOG")
        root = _select(data, operation.response.root or "")
        if not isinstance(root, list):
            raise ProviderError("Provider catalog mapping did not resolve to a list.")
        fields = operation.response.fields
        products: list[ProviderProductDTO] = []
        for item in root:
            if not isinstance(item, dict):
                continue
            external_id = _string(_select(item, fields.get("external_id"))).strip()
            name = _string(_select(item, fields.get("name"))).strip()
            if not external_id or not name:
                raise ProviderError("Provider catalog item is missing external_id or name.")
            products.append(
                ProviderProductDTO(
                    external_id=external_id,
                    name=name,
                    cost=_decimal(_select(item, fields.get("cost"))),
                    currency=_string(
                        _select(item, fields.get("currency"), default="USD"),
                        default="USD",
                    ).upper(),
                    is_available=bool(_select(item, fields.get("is_available"), default=True)),
                    min_quantity=_int_or_none(_select(item, fields.get("min_quantity"))) or 1,
                    max_quantity=_int_or_none(_select(item, fields.get("max_quantity"))) or 100000,
                    stock=_int_or_none(_select(item, fields.get("stock"))),
                )
            )
        return products

    async def get_product(self, external_id: str) -> ProviderProductDTO:
        if "PRODUCT_DETAIL" not in self.mapping.operations:
            for product in await self.list_products():
                if product.external_id == external_id:
                    return product
            raise ProviderProductUnavailableError("Provider product was not found.")
        data, operation = await self._request_json(
            "PRODUCT_DETAIL",
            {"external_id": external_id},
        )
        root = _select(data, operation.response.root or "")
        if not isinstance(root, dict):
            raise ProviderError("Provider product mapping did not resolve to an object.")
        fields = operation.response.fields
        return ProviderProductDTO(
            external_id=_string(_select(root, fields.get("external_id"))).strip() or external_id,
            name=_string(_select(root, fields.get("name"))).strip(),
            cost=_decimal(_select(root, fields.get("cost"))),
            currency=_string(_select(root, fields.get("currency"), default="USD"), default="USD").upper(),
            is_available=bool(_select(root, fields.get("is_available"), default=True)),
            min_quantity=_int_or_none(_select(root, fields.get("min_quantity"))) or 1,
            max_quantity=_int_or_none(_select(root, fields.get("max_quantity"))) or 100000,
            stock=_int_or_none(_select(root, fields.get("stock"))),
        )

    async def create_order(self, request: ProviderOrderRequest) -> ProviderOrderResponse:
        context = {
            "external_product_id": request.external_product_id,
            "quantity": request.quantity,
            "recipient": request.recipient,
            "idempotency_key": request.idempotency_key,
            "parameters": request.parameters,
        }
        data, operation = await self._request_json("CREATE_ORDER", context)
        root = _select(data, operation.response.root or "")
        fields = operation.response.fields
        external_id = _string(_select(root, fields.get("external_order_id"))).strip()
        if not external_id:
            raise ProviderError("Provider order response is missing external_order_id.")
        raw_status = _select(root, fields.get("status"), default="PENDING")
        status = self._mapped_state(raw_status, operation.response) or "PENDING"
        return ProviderOrderResponse(
            external_order_id=external_id,
            status=status,
            cost=_decimal(_select(root, fields.get("cost"))),
            is_success=status.upper() not in {"FAILED", "REJECTED", "CANCELLED", "CANCELED"},
            raw_data=_redact_secret_values(
                root if isinstance(root, dict) else {"value": root},
                self.credentials.values(),
            ),
            delivery=_delivery_artifacts(root, operation.response),
        )

    async def get_order(self, external_order_id: str) -> ProviderOrderCheckResponse:
        data, operation = await self._request_json(
            "ORDER_STATUS",
            {"external_order_id": external_order_id},
        )
        root = _select(data, operation.response.root or "")
        fields = operation.response.fields
        status = self._mapped_state(
            _select(root, fields.get("status"), default="UNKNOWN"),
            operation.response,
        ) or "UNKNOWN"
        normalized = status.upper()
        return ProviderOrderCheckResponse(
            external_order_id=_string(
                _select(root, fields.get("external_order_id"), default=external_order_id),
                default=external_order_id,
            ),
            status=status,
            is_completed=normalized in {"COMPLETED", "DONE", "SUCCESS", "SUCCESSFUL"},
            is_failed=normalized in {"FAILED", "REJECTED", "CANCELLED", "CANCELED", "EXPIRED"},
            raw_data=_redact_secret_values(
                root if isinstance(root, dict) else {"value": root},
                self.credentials.values(),
            ),
            delivery=_delivery_artifacts(root, operation.response),
        )

    async def cancel_order(self, external_order_id: str) -> bool:
        if "CANCEL_ORDER" not in self.mapping.operations:
            return False
        data, operation = await self._request_json(
            "CANCEL_ORDER",
            {"external_order_id": external_order_id},
        )
        fields = operation.response.fields
        raw = _select(data, fields.get("success"), default=True)
        return bool(raw)

    async def list_number_services(self) -> list[NumberServiceDTO]:
        data, operation = await self._request_json("NUMBER_SERVICES")
        root = _select(data, operation.response.root or "")
        if not isinstance(root, list):
            raise ProviderError("Number service mapping did not resolve to a list.")
        fields = operation.response.fields
        result: list[NumberServiceDTO] = []
        for item in root:
            code = _string(_select(item, fields.get("code"))).strip()
            name = _string(_select(item, fields.get("name"), default=code), default=code).strip()
            if code:
                result.append(NumberServiceDTO(code=code, name=name))
        return result

    async def list_number_countries(self, service: str | None = None) -> list[NumberCountryDTO]:
        data, operation = await self._request_json("NUMBER_COUNTRIES", {"service": service})
        root = _select(data, operation.response.root or "")
        if not isinstance(root, list):
            raise ProviderError("Number country mapping did not resolve to a list.")
        fields = operation.response.fields
        result: list[NumberCountryDTO] = []
        for item in root:
            code = _string(_select(item, fields.get("code"))).strip().upper()
            name = _string(_select(item, fields.get("name"), default=code), default=code).strip()
            if code:
                result.append(
                    NumberCountryDTO(
                        code=code,
                        name=name,
                        dial_code=_string(_select(item, fields.get("dial_code"))).strip() or None,
                    )
                )
        return result

    async def list_number_offers(
        self,
        *,
        service: str,
        country: str,
    ) -> list[NumberOfferDTO]:
        data, operation = await self._request_json(
            "NUMBER_OFFERS",
            {"service": service, "country": country},
        )
        root = _select(data, operation.response.root or "")
        if not isinstance(root, list):
            raise ProviderError("Number offer mapping did not resolve to a list.")
        fields = operation.response.fields
        result: list[NumberOfferDTO] = []
        for item in root:
            result.append(
                NumberOfferDTO(
                    service=_string(_select(item, fields.get("service"), default=service), default=service),
                    country=_string(_select(item, fields.get("country"), default=country), default=country).upper(),
                    cost=_decimal(_select(item, fields.get("cost"))),
                    currency=_string(_select(item, fields.get("currency"), default="USD"), default="USD").upper(),
                    available_quantity=_int_or_none(_select(item, fields.get("available_quantity"))),
                    operator=_string(_select(item, fields.get("operator"))).strip() or None,
                    provider_offer_id=_string(_select(item, fields.get("provider_offer_id"))).strip() or None,
                )
            )
        return result

    def _number_snapshot(
        self,
        data: Any,
        operation: HttpOperationMapping,
        *,
        fallback_id: str | None = None,
    ) -> NumberActivationSnapshot:
        root = _select(data, operation.response.root or "")
        fields = operation.response.fields
        external_id = _string(
            _select(root, fields.get("external_order_id"), default=fallback_id or ""),
            default=fallback_id or "",
        ).strip()
        if not external_id:
            raise ProviderError("Number activation response is missing external_order_id.")
        raw_status = _select(root, fields.get("status"), default="UNKNOWN")
        mapped = self._mapped_state(raw_status, operation.response).upper()
        try:
            state = NumberActivationState(mapped)
        except ValueError:
            state = NumberActivationState.UNKNOWN
        messages: tuple[SmsMessageDTO, ...] = ()
        messages_path = fields.get("messages")
        if messages_path:
            raw_messages = _select(root, messages_path, default=[])
            if isinstance(raw_messages, list):
                messages = tuple(
                    SmsMessageDTO(
                        code=_string(
                            _select(item, fields.get("message_code") or "code")
                        ).strip() or None,
                        text=_string(
                            _select(item, fields.get("message_text") or "text")
                        ).strip() or None,
                        sender=_string(
                            _select(item, fields.get("message_sender") or "sender")
                        ).strip() or None,
                        received_at=_datetime_or_none(
                            _select(item, fields.get("message_received_at") or "received_at")
                        ),
                    )
                    for item in raw_messages
                    if isinstance(item, dict)
                )
        return NumberActivationSnapshot(
            external_order_id=external_id,
            state=state,
            phone_number=_string(_select(root, fields.get("phone_number"))).strip() or None,
            cost=(
                _decimal(_select(root, fields.get("cost")))
                if fields.get("cost") is not None
                else None
            ),
            currency=_string(_select(root, fields.get("currency"))).strip().upper() or None,
            expires_at=_datetime_or_none(_select(root, fields.get("expires_at"))),
            messages=messages,
            raw_data=_redact_secret_values(
                root if isinstance(root, dict) else {"value": root},
                self.credentials.values(),
            ),
        )

    async def reserve_number(self, request: NumberReservationRequest) -> NumberActivationSnapshot:
        context = {
            "service": request.service,
            "country": request.country,
            "operator": request.operator,
            "max_price": str(request.max_price) if request.max_price is not None else None,
            "idempotency_key": request.idempotency_key,
            "parameters": request.parameters,
        }
        data, operation = await self._request_json("NUMBER_RESERVE", context)
        return self._number_snapshot(data, operation)

    async def get_number_activation(self, external_order_id: str) -> NumberActivationSnapshot:
        data, operation = await self._request_json(
            "NUMBER_ACTIVATION",
            {"external_order_id": external_order_id},
        )
        return self._number_snapshot(data, operation, fallback_id=external_order_id)

    async def cancel_number_activation(self, external_order_id: str) -> NumberActivationSnapshot:
        data, operation = await self._request_json(
            "NUMBER_CANCEL",
            {"external_order_id": external_order_id},
        )
        return self._number_snapshot(data, operation, fallback_id=external_order_id)

    async def finish_number_activation(self, external_order_id: str) -> NumberActivationSnapshot:
        data, operation = await self._request_json(
            "NUMBER_FINISH",
            {"external_order_id": external_order_id},
        )
        return self._number_snapshot(data, operation, fallback_id=external_order_id)
