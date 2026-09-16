from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from packages.core.config import settings
from packages.saas.models import SubscriptionStatus


class BillingProviderError(RuntimeError):
    code = "BILLING_PROVIDER_ERROR"


class BillingProviderNotConfiguredError(BillingProviderError):
    code = "BILLING_PROVIDER_NOT_CONFIGURED"


class BillingWebhookVerificationError(BillingProviderError):
    code = "BILLING_WEBHOOK_VERIFICATION_FAILED"


class BillingProviderResponseError(BillingProviderError):
    code = "BILLING_PROVIDER_RESPONSE_ERROR"


@dataclass(frozen=True, slots=True)
class CheckoutSessionResult:
    provider: str
    external_session_id: str
    url: str
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PortalSessionResult:
    provider: str
    external_session_id: str
    url: str


@dataclass(frozen=True, slots=True)
class ProviderSubscriptionState:
    provider: str
    event_type: str
    external_event_id: str | None
    tenant_id: uuid.UUID
    external_price_id: str
    status: SubscriptionStatus
    external_customer_id: str | None
    external_subscription_id: str
    current_period_start: datetime | None
    current_period_end: datetime | None
    trial_ends_at: datetime | None
    cancel_at_period_end: bool
    provider_created_at: datetime
    revision: str
    event_metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class VerifiedWebhookEvent:
    provider: str
    external_event_id: str
    event_type: str
    provider_created_at: datetime
    subscription_state: ProviderSubscriptionState | None
    external_subscription_id: str | None = None
    tenant_id: uuid.UUID | None = None


class BillingProviderAdapter(Protocol):
    provider_name: str

    async def create_checkout_session(
        self,
        *,
        tenant_id: uuid.UUID,
        tenant_name: str,
        plan_key: str,
        external_price_id: str,
        success_url: str,
        cancel_url: str,
        idempotency_key: str,
        existing_customer_id: str | None,
    ) -> CheckoutSessionResult: ...

    async def create_portal_session(
        self,
        *,
        external_customer_id: str,
        return_url: str,
    ) -> PortalSessionResult: ...

    def verify_webhook(self, payload: bytes, signature_header: str) -> VerifiedWebhookEvent: ...

    async def fetch_subscription(self, external_subscription_id: str) -> ProviderSubscriptionState: ...


class StripeBillingAdapter:
    provider_name = "stripe"
    api_base = "https://api.stripe.com/v1"

    def __init__(
        self,
        *,
        secret_key: str,
        webhook_secret: str,
        webhook_tolerance_seconds: int = 300,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._secret_key = secret_key.strip()
        self._webhook_secret = webhook_secret.strip()
        self._webhook_tolerance_seconds = webhook_tolerance_seconds
        self._http_client = http_client
        if not self._secret_key:
            raise BillingProviderNotConfiguredError("STRIPE_SECRET_KEY is not configured.")
        if not self._webhook_secret:
            raise BillingProviderNotConfiguredError("STRIPE_WEBHOOK_SECRET is not configured.")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._secret_key}",
            "Accept": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key[:255]
        owns_client = self._http_client is None
        client = self._http_client or httpx.AsyncClient(timeout=15.0)
        try:
            response = await client.request(
                method,
                f"{self.api_base}{path}",
                headers=headers,
                data=data,
            )
        except httpx.HTTPError as exc:
            raise BillingProviderResponseError("Stripe API request failed.") from exc
        finally:
            if owns_client:
                await client.aclose()
        if response.status_code >= 400:
            request_id = response.headers.get("request-id") or response.headers.get("Request-Id")
            suffix = f" Request id: {request_id}." if request_id else ""
            raise BillingProviderResponseError(
                f"Stripe API returned HTTP {response.status_code}.{suffix}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise BillingProviderResponseError("Stripe API returned invalid JSON.") from exc
        if not isinstance(body, dict):
            raise BillingProviderResponseError("Stripe API returned an unexpected response.")
        return body

    async def create_checkout_session(
        self,
        *,
        tenant_id: uuid.UUID,
        tenant_name: str,
        plan_key: str,
        external_price_id: str,
        success_url: str,
        cancel_url: str,
        idempotency_key: str,
        existing_customer_id: str | None,
    ) -> CheckoutSessionResult:
        metadata = {
            "ghbf_tenant_id": str(tenant_id),
            "ghbf_plan_key": plan_key,
            "ghbf_price_id": external_price_id,
        }
        data: dict[str, Any] = {
            "mode": "subscription",
            "line_items[0][price]": external_price_id,
            "line_items[0][quantity]": "1",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": str(tenant_id),
            "subscription_data[metadata][ghbf_tenant_id]": metadata["ghbf_tenant_id"],
            "subscription_data[metadata][ghbf_plan_key]": plan_key,
            "subscription_data[metadata][ghbf_price_id]": external_price_id,
            "metadata[ghbf_tenant_id]": metadata["ghbf_tenant_id"],
            "metadata[ghbf_plan_key]": plan_key,
            "metadata[ghbf_price_id]": external_price_id,
            "metadata[ghbf_tenant_name]": tenant_name[:120],
        }
        if existing_customer_id:
            data["customer"] = existing_customer_id
        body = await self._request(
            "POST",
            "/checkout/sessions",
            data=data,
            idempotency_key=idempotency_key,
        )
        session_id = str(body.get("id") or "").strip()
        url = str(body.get("url") or "").strip()
        if not session_id or not url.startswith("https://"):
            raise BillingProviderResponseError("Stripe checkout response is missing a secure URL.")
        expires_at = _timestamp(body.get("expires_at"))
        return CheckoutSessionResult(
            provider=self.provider_name,
            external_session_id=session_id,
            url=url,
            expires_at=expires_at,
        )

    async def create_portal_session(
        self,
        *,
        external_customer_id: str,
        return_url: str,
    ) -> PortalSessionResult:
        body = await self._request(
            "POST",
            "/billing_portal/sessions",
            data={"customer": external_customer_id, "return_url": return_url},
        )
        session_id = str(body.get("id") or "").strip()
        url = str(body.get("url") or "").strip()
        if not session_id or not url.startswith("https://"):
            raise BillingProviderResponseError("Stripe portal response is missing a secure URL.")
        return PortalSessionResult(
            provider=self.provider_name,
            external_session_id=session_id,
            url=url,
        )

    def verify_webhook(self, payload: bytes, signature_header: str) -> VerifiedWebhookEvent:
        timestamp, signatures = _parse_stripe_signature(signature_header)
        now = int(time.time())
        if abs(now - timestamp) > self._webhook_tolerance_seconds:
            raise BillingWebhookVerificationError("Stripe webhook timestamp is outside tolerance.")
        signed_payload = str(timestamp).encode("ascii") + b"." + payload
        expected = hmac.new(
            self._webhook_secret.encode("utf-8"),
            signed_payload,
            hashlib.sha256,
        ).hexdigest()
        if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
            raise BillingWebhookVerificationError("Stripe webhook signature is invalid.")
        try:
            event = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BillingWebhookVerificationError("Stripe webhook payload is invalid JSON.") from exc
        if not isinstance(event, dict):
            raise BillingWebhookVerificationError("Stripe webhook payload is not an object.")
        event_id = str(event.get("id") or "").strip()
        event_type = str(event.get("type") or "").strip()
        created = _timestamp(event.get("created")) or datetime.now(UTC)
        if not event_id or not event_type:
            raise BillingWebhookVerificationError("Stripe webhook event is missing identity fields.")
        data = event.get("data")
        obj = data.get("object") if isinstance(data, dict) else None
        if not isinstance(obj, dict):
            raise BillingWebhookVerificationError("Stripe webhook event is missing data.object.")

        if event_type.startswith("customer.subscription."):
            state = self._normalize_subscription_object(
                obj,
                event_type=event_type,
                external_event_id=event_id,
                provider_created_at=created,
            )
            return VerifiedWebhookEvent(
                provider=self.provider_name,
                external_event_id=event_id,
                event_type=event_type,
                provider_created_at=created,
                subscription_state=state,
                external_subscription_id=state.external_subscription_id,
                tenant_id=state.tenant_id,
            )

        if event_type == "checkout.session.completed":
            metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
            tenant_id = _metadata_tenant_id(metadata, fallback=obj.get("client_reference_id"))
            subscription_id = _string_or_none(obj.get("subscription"))
            if subscription_id is None:
                raise BillingWebhookVerificationError(
                    "Completed subscription checkout is missing subscription id."
                )
            return VerifiedWebhookEvent(
                provider=self.provider_name,
                external_event_id=event_id,
                event_type=event_type,
                provider_created_at=created,
                subscription_state=None,
                external_subscription_id=subscription_id,
                tenant_id=tenant_id,
            )

        return VerifiedWebhookEvent(
            provider=self.provider_name,
            external_event_id=event_id,
            event_type=event_type,
            provider_created_at=created,
            subscription_state=None,
        )

    async def fetch_subscription(self, external_subscription_id: str) -> ProviderSubscriptionState:
        body = await self._request("GET", f"/subscriptions/{external_subscription_id}")
        observed_at = datetime.now(UTC)
        revision_seed = json.dumps(
            {
                "id": body.get("id"),
                "status": body.get("status"),
                "cancel_at_period_end": body.get("cancel_at_period_end"),
                "current_period_start": body.get("current_period_start"),
                "current_period_end": body.get("current_period_end"),
                "trial_end": body.get("trial_end"),
                "items": body.get("items"),
                "metadata": body.get("metadata"),
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        revision = hashlib.sha256(revision_seed).hexdigest()[:32]
        return self._normalize_subscription_object(
            body,
            event_type="subscription.reconciled",
            external_event_id=None,
            provider_created_at=observed_at,
            revision=revision,
        )

    def _normalize_subscription_object(
        self,
        obj: dict[str, Any],
        *,
        event_type: str,
        external_event_id: str | None,
        provider_created_at: datetime,
        revision: str | None = None,
    ) -> ProviderSubscriptionState:
        metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
        tenant_id = _metadata_tenant_id(metadata)
        external_price_id = _stripe_subscription_price_id(obj)
        external_subscription_id = str(obj.get("id") or "").strip()
        if not external_subscription_id:
            raise BillingProviderResponseError("Stripe subscription is missing its id.")
        status = _stripe_subscription_status(str(obj.get("status") or ""))
        if revision is None:
            revision_seed = f"{external_event_id or ''}:{external_subscription_id}:{status.value}:{external_price_id}"
            revision = hashlib.sha256(revision_seed.encode("utf-8")).hexdigest()[:32]
        return ProviderSubscriptionState(
            provider=self.provider_name,
            event_type=event_type,
            external_event_id=external_event_id,
            tenant_id=tenant_id,
            external_price_id=external_price_id,
            status=status,
            external_customer_id=_string_or_none(obj.get("customer")),
            external_subscription_id=external_subscription_id,
            current_period_start=_timestamp(obj.get("current_period_start")),
            current_period_end=_timestamp(obj.get("current_period_end")),
            trial_ends_at=_timestamp(obj.get("trial_end")),
            cancel_at_period_end=bool(obj.get("cancel_at_period_end", False)),
            provider_created_at=provider_created_at,
            revision=revision,
            event_metadata={"source": "stripe", "external_price_id": external_price_id},
        )


def get_billing_provider(provider: str | None = None) -> BillingProviderAdapter:
    configured = settings.billing_provider.strip().lower()
    selected = (provider or configured).strip().lower()
    if selected != configured:
        raise BillingProviderNotConfiguredError(
            f"Billing provider '{selected}' is not the configured installation provider."
        )
    if selected == "stripe":
        return StripeBillingAdapter(
            secret_key=settings.stripe_secret_key or "",
            webhook_secret=settings.stripe_webhook_secret or "",
            webhook_tolerance_seconds=settings.billing_webhook_tolerance_seconds,
        )
    raise BillingProviderNotConfiguredError("No hosted SaaS billing provider is configured.")


def _parse_stripe_signature(value: str) -> tuple[int, list[str]]:
    timestamp: int | None = None
    signatures: list[str] = []
    for part in (value or "").split(","):
        key, sep, raw = part.strip().partition("=")
        if not sep:
            continue
        if key == "t":
            try:
                timestamp = int(raw)
            except ValueError as exc:
                raise BillingWebhookVerificationError("Stripe signature timestamp is invalid.") from exc
        elif key == "v1" and raw:
            signatures.append(raw)
    if timestamp is None or not signatures:
        raise BillingWebhookVerificationError("Stripe signature header is incomplete.")
    return timestamp, signatures


def _timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OSError) as exc:
        raise BillingProviderResponseError("Billing provider returned an invalid timestamp.") from exc


def _metadata_tenant_id(metadata: dict[str, Any], fallback: Any = None) -> uuid.UUID:
    raw = metadata.get("ghbf_tenant_id") or fallback
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError, AttributeError) as exc:
        raise BillingProviderResponseError(
            "Billing provider subscription is missing valid GHBF tenant metadata."
        ) from exc


def _stripe_subscription_price_id(obj: dict[str, Any]) -> str:
    items = obj.get("items")
    data = items.get("data") if isinstance(items, dict) else None
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise BillingProviderResponseError(
            "GHBF subscriptions must contain exactly one recurring billing price."
        )
    price = data[0].get("price")
    price_id = price.get("id") if isinstance(price, dict) else None
    if not isinstance(price_id, str) or not price_id.strip():
        raise BillingProviderResponseError("Stripe subscription item is missing price id.")
    return price_id.strip()


def _stripe_subscription_status(value: str) -> SubscriptionStatus:
    normalized = value.strip().lower()
    mapping = {
        "trialing": SubscriptionStatus.TRIALING,
        "active": SubscriptionStatus.ACTIVE,
        "past_due": SubscriptionStatus.PAST_DUE,
        "unpaid": SubscriptionStatus.PAST_DUE,
        "paused": SubscriptionStatus.PAUSED,
        "canceled": SubscriptionStatus.CANCELED,
        "incomplete": SubscriptionStatus.PAUSED,
        "incomplete_expired": SubscriptionStatus.CANCELED,
    }
    try:
        return mapping[normalized]
    except KeyError as exc:
        raise BillingProviderResponseError(
            f"Unsupported Stripe subscription status '{normalized or 'missing'}'."
        ) from exc


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
