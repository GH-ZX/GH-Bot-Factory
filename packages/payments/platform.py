from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.payments.exceptions import PaymentError, PaymentIntegrityError
from packages.payments.models import (
    PaymentAssuranceLevel,
    PaymentIntent,
    PaymentIntentPurpose,
    PaymentMethodConfig,
    PaymentMethodType,
    PaymentObservation,
    PaymentObservationSource,
    PaymentObservationStatus,
    PaymentQuote,
    PaymentVerificationMode,
)
from packages.payments.payment_service import PaymentService
from packages.payments.state_machine import PaymentIntentStatus

_METHOD_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_SECRET_KEY_RE = re.compile(r"(?:secret|password|private[_-]?key|api[_-]?key|token|seed|mnemonic)", re.IGNORECASE)


@dataclass(frozen=True)
class OnChainVerificationResult:
    network: str
    tx_hash: str
    asset: str
    destination_address: str
    asset_amount: Decimal
    confirmations: int
    is_final: bool
    succeeded: bool
    observed_at: datetime | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class OnChainVerifier(Protocol):
    verifier_name: str

    async def verify_transaction(
        self,
        network: str,
        tx_hash: str,
        *,
        expected_asset: str | None = None,
        expected_destination: str | None = None,
    ) -> OnChainVerificationResult:
        ...


class OnChainVerifierRegistry:
    """Small explicit registry for trusted chain verifiers.

    No chain adapter is auto-loaded from tenant input. Only application-registered verifier
    instances can become authoritative evidence sources.
    """

    def __init__(self) -> None:
        self._verifiers: dict[str, OnChainVerifier] = {}

    def register(self, network: str, verifier: OnChainVerifier) -> None:
        self._verifiers[network.strip().upper()] = verifier

    def get(self, network: str) -> OnChainVerifier:
        verifier = self._verifiers.get(network.strip().upper())
        if verifier is None:
            raise PaymentError(f"No trusted on-chain verifier is registered for network {network}.")
        return verifier

    def has(self, network: str) -> bool:
        return network.strip().upper() in self._verifiers

    def registered_networks(self) -> tuple[str, ...]:
        return tuple(sorted(self._verifiers))


default_onchain_verifier_registry = OnChainVerifierRegistry()


class PaymentPlatformService:
    """Financial-grade payment-method and payment-evidence orchestration.

    Core invariant: external evidence never mutates wallet balance directly. Evidence must
    first converge to VERIFIED through a trusted verifier or privileged manual approval;
    only then does the existing PaymentService ledger settlement gate execute exactly once.
    """

    def __init__(
        self,
        payment_service: PaymentService | None = None,
        onchain_registry: OnChainVerifierRegistry | None = None,
    ) -> None:
        self.payment_service = payment_service or PaymentService()
        self.onchain_registry = onchain_registry or default_onchain_verifier_registry

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        # SQLite drops timezone metadata even for timezone-aware DateTime columns.
        # Stored Phase 11 timestamps are UTC by contract, so normalize round-tripped
        # naive values back to UTC before any security-sensitive expiry comparison.
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _normalize_code(code: str) -> str:
        normalized = code.strip().lower()
        if not _METHOD_CODE_RE.fullmatch(normalized):
            raise PaymentError(
                "Payment method code must be 2-64 lowercase letters/digits with optional '-' or '_'."
            )
        return normalized

    @staticmethod
    def _normalize_asset(asset: str | None) -> str | None:
        normalized = (asset or "").strip().upper() or None
        if normalized is not None and (len(normalized) > 24 or not re.fullmatch(r"[A-Z0-9._-]+", normalized)):
            raise PaymentError("Payment asset identifier is invalid.")
        return normalized

    @staticmethod
    def _normalize_network(network: str | None) -> str | None:
        normalized = (network or "").strip().upper() or None
        if normalized is not None and (len(normalized) > 64 or not re.fullmatch(r"[A-Z0-9._:-]+", normalized)):
            raise PaymentError("Payment network identifier is invalid.")
        return normalized

    @staticmethod
    def _normalize_onchain_reference(reference: str) -> str:
        normalized = reference.strip()
        # EVM/TRON transaction hashes are case-insensitive hexadecimal identities. Store one
        # canonical form before fingerprints/unique constraints so casing cannot bypass replay
        # protection across tenants. Unknown non-hex chain references are preserved verbatim.
        if re.fullmatch(r"0x[0-9A-Fa-f]{64}", normalized):
            return normalized.lower()
        if re.fullmatch(r"[0-9A-Fa-f]{64}", normalized):
            return normalized.lower()
        return normalized

    @staticmethod
    def _assert_public_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
        result = dict(settings or {})

        def walk(value: Any, path: str = "settings") -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if _SECRET_KEY_RE.search(str(key)):
                        raise PaymentIntegrityError(
                            f"Private credential-like field '{path}.{key}' is forbidden in payment method settings."
                        )
                    walk(child, f"{path}.{key}")
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    walk(child, f"{path}[{index}]")

        walk(result)
        return result

    @classmethod
    def _validate_method_shape(
        cls,
        *,
        method_type: PaymentMethodType,
        verification_mode: PaymentVerificationMode,
        provider_name: str | None,
        asset: str | None,
        network: str | None,
        destination_address: str | None,
        requires_admin_approval: bool,
    ) -> None:
        provider = (provider_name or "").strip().lower() or None
        destination = (destination_address or "").strip() or None
        if method_type in {PaymentMethodType.REGULATED_PROVIDER, PaymentMethodType.CRYPTO_GATEWAY}:
            if not provider:
                raise PaymentError("Provider-backed payment methods require provider_name.")
            if verification_mode not in {
                PaymentVerificationMode.PROVIDER_RECONCILIATION,
                PaymentVerificationMode.HYBRID,
            }:
                raise PaymentError(
                    "Provider-backed payment methods require PROVIDER_RECONCILIATION or HYBRID verification."
                )
        if verification_mode in {PaymentVerificationMode.ONCHAIN, PaymentVerificationMode.HYBRID} and (not asset or not network or not destination):
            raise PaymentError("On-chain payment methods require asset, network, and destination_address.")
        if method_type == PaymentMethodType.SELF_CUSTODY and verification_mode not in {
            PaymentVerificationMode.ONCHAIN,
            PaymentVerificationMode.HYBRID,
        }:
            raise PaymentError("Self-custody payment methods require ONCHAIN or HYBRID verification.")
        if method_type in {PaymentMethodType.MANUAL_TRANSFER, PaymentMethodType.EXCHANGE_TRANSFER}:
            if verification_mode not in {PaymentVerificationMode.MANUAL, PaymentVerificationMode.HYBRID}:
                raise PaymentError("Manual/exchange payment methods require MANUAL or HYBRID verification.")
            if not requires_admin_approval:
                raise PaymentError("Manual/exchange payment methods must require admin approval.")

    async def create_method(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        code: str,
        display_name: str,
        method_type: PaymentMethodType,
        verification_mode: PaymentVerificationMode,
        provider_name: str | None = None,
        asset: str | None = None,
        network: str | None = None,
        destination_address: str | None = None,
        destination_memo: str | None = None,
        instructions: str | None = None,
        is_enabled: bool = True,
        requires_admin_approval: bool = False,
        auto_credit_enabled: bool = False,
        auto_credit_target: str = "ASSET_WALLET",
        settings: dict[str, Any] | None = None,
    ) -> PaymentMethodConfig:
        normalized_code = self._normalize_code(code)
        normalized_asset = self._normalize_asset(asset)
        normalized_network = self._normalize_network(network)
        normalized_provider = (provider_name or "").strip().lower() or None
        normalized_destination = (destination_address or "").strip() or None
        public_settings = self._assert_public_settings(settings)
        self._validate_method_shape(
            method_type=method_type,
            verification_mode=verification_mode,
            provider_name=normalized_provider,
            asset=normalized_asset,
            network=normalized_network,
            destination_address=normalized_destination,
            requires_admin_approval=requires_admin_approval,
        )
        normalized_auto_credit_target = str(auto_credit_target or "ASSET_WALLET").strip().upper()
        if normalized_auto_credit_target not in {"ASSET_WALLET", "SETTLEMENT_WALLET"}:
            raise PaymentError("auto_credit_target must be ASSET_WALLET or SETTLEMENT_WALLET.")
        name = display_name.strip()
        if not name or len(name) > 120:
            raise PaymentError("Payment method display name is required and must be <= 120 characters.")

        method = PaymentMethodConfig(
            tenant_id=tenant_id,
            code=normalized_code,
            display_name=name,
            method_type=method_type,
            verification_mode=verification_mode,
            provider_name=normalized_provider,
            asset=normalized_asset,
            network=normalized_network,
            destination_address=normalized_destination,
            destination_memo=(destination_memo or "").strip() or None,
            instructions=(instructions or "").strip() or None,
            is_enabled=is_enabled,
            requires_admin_approval=requires_admin_approval,
            auto_credit_enabled=bool(auto_credit_enabled),
            auto_credit_target=normalized_auto_credit_target,
            settings_json=public_settings,
        )
        try:
            async with session.begin_nested():
                session.add(method)
                await session.flush()
        except IntegrityError as exc:
            raise PaymentIntegrityError(
                f"Payment method code '{normalized_code}' already exists for this tenant."
            ) from exc
        return method

    async def get_method(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        method_id: uuid.UUID,
        enabled_only: bool = False,
    ) -> PaymentMethodConfig:
        stmt = select(PaymentMethodConfig).where(
            PaymentMethodConfig.id == method_id,
            PaymentMethodConfig.tenant_id == tenant_id,
        )
        if enabled_only:
            stmt = stmt.where(PaymentMethodConfig.is_enabled.is_(True))
        method = (await session.execute(stmt)).scalar_one_or_none()
        if method is None:
            raise PaymentError("Payment method not found or unavailable.")
        return method

    async def list_methods(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        enabled_only: bool = False,
    ) -> list[PaymentMethodConfig]:
        stmt = select(PaymentMethodConfig).where(PaymentMethodConfig.tenant_id == tenant_id)
        if enabled_only:
            stmt = stmt.where(PaymentMethodConfig.is_enabled.is_(True))
        stmt = stmt.order_by(PaymentMethodConfig.display_name.asc(), PaymentMethodConfig.id.asc())
        return list((await session.execute(stmt)).scalars().all())

    async def update_method(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        method_id: uuid.UUID,
        display_name: str | None = None,
        provider_name: str | None = None,
        asset: str | None = None,
        network: str | None = None,
        destination_address: str | None = None,
        destination_memo: str | None = None,
        instructions: str | None = None,
        requires_admin_approval: bool | None = None,
        auto_credit_enabled: bool | None = None,
        auto_credit_target: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> PaymentMethodConfig:
        method = await self.get_method(session, tenant_id=tenant_id, method_id=method_id)
        effective_provider = (provider_name if provider_name is not None else method.provider_name)
        effective_asset = self._normalize_asset(asset if asset is not None else method.asset)
        effective_network = self._normalize_network(network if network is not None else method.network)
        effective_destination = (
            destination_address if destination_address is not None else method.destination_address
        )
        effective_approval = (
            requires_admin_approval
            if requires_admin_approval is not None
            else method.requires_admin_approval
        )
        self._validate_method_shape(
            method_type=method.method_type,
            verification_mode=method.verification_mode,
            provider_name=effective_provider,
            asset=effective_asset,
            network=effective_network,
            destination_address=effective_destination,
            requires_admin_approval=effective_approval,
        )
        if display_name is not None:
            normalized_name = display_name.strip()
            if not normalized_name or len(normalized_name) > 120:
                raise PaymentError(
                    "Payment method display name is required and must be <= 120 characters."
                )
            method.display_name = normalized_name
        if provider_name is not None:
            method.provider_name = provider_name.strip().lower() or None
        if asset is not None:
            method.asset = effective_asset
        if network is not None:
            method.network = effective_network
        if destination_address is not None:
            method.destination_address = destination_address.strip() or None
        if destination_memo is not None:
            method.destination_memo = destination_memo.strip() or None
        if instructions is not None:
            method.instructions = instructions.strip() or None
        if requires_admin_approval is not None:
            method.requires_admin_approval = requires_admin_approval
        if auto_credit_enabled is not None:
            method.auto_credit_enabled = bool(auto_credit_enabled)
        if auto_credit_target is not None:
            normalized_target = auto_credit_target.strip().upper()
            if normalized_target not in {"ASSET_WALLET", "SETTLEMENT_WALLET"}:
                raise PaymentError("auto_credit_target must be ASSET_WALLET or SETTLEMENT_WALLET.")
            method.auto_credit_target = normalized_target
        if settings is not None:
            method.settings_json = self._assert_public_settings(settings)
        await session.flush()
        return method

    async def set_method_enabled(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        method_id: uuid.UUID,
        is_enabled: bool,
    ) -> PaymentMethodConfig:
        method = await self.get_method(session, tenant_id=tenant_id, method_id=method_id)
        method.is_enabled = is_enabled
        await session.flush()
        return method

    @staticmethod
    def _normalize_topup_amount(amount: Decimal, currency: str) -> tuple[Decimal, str]:
        normalized_currency = currency.strip().upper()
        if len(normalized_currency) != 3 or not normalized_currency.isalpha():
            raise PaymentError("Wallet settlement currency must be a three-letter code.")
        try:
            normalized_amount = amount.quantize(Decimal("0.01"))
        except (InvalidOperation, AttributeError) as exc:
            raise PaymentError("Wallet top-up amount is invalid.") from exc
        if normalized_amount != amount or normalized_amount <= Decimal("0.00"):
            raise PaymentError("Wallet top-up amount must be positive with at most two decimals.")
        return normalized_amount, normalized_currency

    @staticmethod
    def _validate_topup_policy(
        method: PaymentMethodConfig,
        amount: Decimal,
        currency: str,
    ) -> None:
        settings = method.settings_json or {}
        try:
            min_amount = Decimal(str(settings.get("topup_min_amount", "1.00")))
            max_amount = Decimal(str(settings.get("topup_max_amount", "1000.00")))
        except InvalidOperation as exc:
            raise PaymentIntegrityError("Payment method contains invalid top-up limits.") from exc
        if min_amount <= 0 or max_amount < min_amount:
            raise PaymentIntegrityError("Payment method contains invalid top-up limits.")
        currencies = {
            str(item).strip().upper()
            for item in settings.get("topup_currencies", ["USD"])
            if str(item).strip()
        }
        if currency not in currencies:
            raise PaymentError(f"Currency {currency} is not enabled for this payment method.")
        if amount < min_amount or amount > max_amount:
            raise PaymentError(
                f"Wallet top-up amount must be between {min_amount} and {max_amount} {currency}."
            )

    async def create_topup_intent(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        method_id: uuid.UUID,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
        provider_context: dict[str, str] | None = None,
    ) -> PaymentIntent:
        """Create a top-up through either a local/manual or provider-backed method.

        PaymentMethodConfig is the customer-facing selection layer. Provider-backed
        methods delegate creation to PaymentService so gateway polling/webhook
        reconciliation and the existing exactly-once ledger gate remain authoritative.
        """
        method = await self.get_method(
            session, tenant_id=tenant_id, method_id=method_id, enabled_only=True
        )
        normalized_amount, normalized_currency = self._normalize_topup_amount(amount, currency)
        self._validate_topup_policy(method, normalized_amount, normalized_currency)
        if method.method_type not in {
            PaymentMethodType.REGULATED_PROVIDER,
            PaymentMethodType.CRYPTO_GATEWAY,
        }:
            return await self.create_local_topup_intent(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                method_id=method.id,
                amount=normalized_amount,
                currency=normalized_currency,
                idempotency_key=idempotency_key,
            )

        provider_name = (method.provider_name or "").strip().lower()
        if not provider_name:
            raise PaymentIntegrityError("Provider-backed payment method is missing provider_name.")
        settings = method.settings_json or {}
        provider_pay_currency = str(settings.get("provider_pay_currency") or "").strip().lower()
        metadata: dict[str, Any] = {
            "source": "payment_method",
            "payment_method_code": method.code,
        }
        if provider_pay_currency:
            metadata["provider_pay_currency"] = provider_pay_currency
        intent = await self.payment_service.create_wallet_topup_intent(
            session=session,
            tenant_id=tenant_id,
            user_id=user_id,
            amount=normalized_amount,
            currency=normalized_currency,
            provider_name=provider_name,
            idempotency_key=idempotency_key,
            metadata=metadata,
            payment_method_id=method.id,
            provider_context=provider_context,
        )
        return intent

    async def create_local_topup_intent(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        method_id: uuid.UUID,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
    ) -> PaymentIntent:
        if not idempotency_key or len(idempotency_key) > 100:
            raise PaymentError("A valid idempotency key is required.")
        method = await self.get_method(
            session, tenant_id=tenant_id, method_id=method_id, enabled_only=True
        )
        if method.method_type in {
            PaymentMethodType.REGULATED_PROVIDER,
            PaymentMethodType.CRYPTO_GATEWAY,
        }:
            raise PaymentError(
                "Provider-backed payment methods must be initialized through the provider top-up flow."
            )
        normalized_amount, normalized_currency = self._normalize_topup_amount(amount, currency)
        self._validate_topup_policy(method, normalized_amount, normalized_currency)

        existing_stmt = select(PaymentIntent).where(
            PaymentIntent.tenant_id == tenant_id,
            PaymentIntent.idempotency_key == idempotency_key,
        )
        existing = (await session.execute(existing_stmt)).scalar_one_or_none()
        if existing is not None:
            if (
                existing.purpose != PaymentIntentPurpose.WALLET_TOPUP
                or existing.user_id != user_id
                or existing.payment_method_id != method.id
                or existing.amount != normalized_amount
                or existing.currency != normalized_currency
            ):
                raise PaymentIntegrityError(
                    f"Idempotency key {idempotency_key} is already bound to a different payment request."
                )
            return existing

        instruction_snapshot = {
            "method_code": method.code,
            "display_name": method.display_name,
            "method_type": method.method_type.value,
            "verification_mode": method.verification_mode.value,
            "asset": method.asset,
            "network": method.network,
            "destination_address": method.destination_address,
            "destination_memo": method.destination_memo,
            "instructions": method.instructions,
        }
        intent = PaymentIntent(
            tenant_id=tenant_id,
            order_id=None,
            purpose=PaymentIntentPurpose.WALLET_TOPUP,
            user_id=user_id,
            payment_method_id=method.id,
            provider=f"method:{method.code}",
            currency=normalized_currency,
            amount=normalized_amount,
            status=PaymentIntentStatus.PENDING,
            idempotency_key=idempotency_key,
            metadata_json={"payment_instruction": instruction_snapshot},
        )
        try:
            async with session.begin_nested():
                session.add(intent)
                await session.flush()
        except IntegrityError as exc:
            concurrent = (await session.execute(existing_stmt)).scalar_one_or_none()
            if concurrent is not None and (
                concurrent.purpose == PaymentIntentPurpose.WALLET_TOPUP
                and concurrent.user_id == user_id
                and concurrent.payment_method_id == method.id
                and concurrent.amount == normalized_amount
                and concurrent.currency == normalized_currency
            ):
                return concurrent
            raise PaymentIntegrityError(
                f"Concurrent payment-method top-up conflict for key {idempotency_key}."
            ) from exc
        return intent

    @staticmethod
    def _quote_fingerprint(
        *,
        tenant_id: uuid.UUID,
        intent_id: uuid.UUID,
        method_id: uuid.UUID,
        settlement_amount: Decimal,
        settlement_currency: str,
        asset_amount: Decimal,
        asset: str,
        network: str,
        rate: Decimal,
        rate_source: str,
        quote_reference: str | None,
        expires_at: datetime,
    ) -> str:
        payload = {
            "tenant_id": str(tenant_id),
            "intent_id": str(intent_id),
            "method_id": str(method_id),
            "settlement_amount": format(settlement_amount, "f"),
            "settlement_currency": settlement_currency,
            "asset_amount": format(asset_amount, "f"),
            "asset": asset,
            "network": network,
            "rate": format(rate, "f"),
            "rate_source": rate_source,
            "quote_reference": quote_reference,
            "expires_at": PaymentPlatformService._as_utc(expires_at).isoformat(),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    async def record_quote(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        intent_id: uuid.UUID,
        settlement_amount: Decimal,
        settlement_currency: str,
        asset_amount: Decimal,
        asset: str,
        network: str,
        rate: Decimal,
        rate_source: str,
        expires_at: datetime,
        quote_reference: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PaymentQuote:
        """Persist an immutable server-authoritative asset quote.

        This is intentionally not exposed as a customer-write API. Future gateway/FX adapters
        must create quotes through trusted server-side code, preventing client-supplied FX from
        becoming settlement authority.
        """
        intent = await self.payment_service.get_payment_intent(session, tenant_id, intent_id)
        if intent.payment_method_id is None:
            raise PaymentError("Payment intent is not associated with a Phase 11 payment method.")
        method = await self.get_method(
            session, tenant_id=tenant_id, method_id=intent.payment_method_id
        )
        normalized_currency = settlement_currency.strip().upper()
        normalized_asset = self._normalize_asset(asset)
        normalized_network = self._normalize_network(network)
        if settlement_amount != intent.amount or normalized_currency != intent.currency:
            raise PaymentIntegrityError(
                "Quote settlement amount/currency must match the authoritative payment intent."
            )
        if normalized_asset != self._normalize_asset(method.asset):
            raise PaymentIntegrityError("Quote asset does not match the payment method asset.")
        if normalized_network != self._normalize_network(method.network):
            raise PaymentIntegrityError("Quote network does not match the payment method network.")
        if asset_amount <= 0 or rate <= 0:
            raise PaymentError("Quote asset amount and rate must be strictly positive.")
        now = datetime.now(UTC)
        if expires_at.tzinfo is None:
            raise PaymentError("Quote expiry must be timezone-aware.")
        if expires_at <= now:
            raise PaymentError("Quote expiry must be in the future.")
        source = rate_source.strip()
        if not source or len(source) > 100:
            raise PaymentError("Quote rate_source is required and must be <= 100 characters.")
        fingerprint = self._quote_fingerprint(
            tenant_id=tenant_id,
            intent_id=intent.id,
            method_id=method.id,
            settlement_amount=settlement_amount,
            settlement_currency=normalized_currency,
            asset_amount=asset_amount,
            asset=normalized_asset or "",
            network=normalized_network or "",
            rate=rate,
            rate_source=source,
            quote_reference=(quote_reference or "").strip() or None,
            expires_at=expires_at,
        )
        existing = (
            await session.execute(
                select(PaymentQuote).where(
                    PaymentQuote.tenant_id == tenant_id,
                    PaymentQuote.quote_fingerprint == fingerprint,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        quote = PaymentQuote(
            tenant_id=tenant_id,
            payment_intent_id=intent.id,
            payment_method_id=method.id,
            settlement_amount=settlement_amount,
            settlement_currency=normalized_currency,
            asset_amount=asset_amount,
            asset=normalized_asset or "",
            network=normalized_network or "",
            rate=rate,
            rate_source=source,
            quote_reference=(quote_reference or "").strip() or None,
            quote_fingerprint=fingerprint,
            expires_at=expires_at,
            metadata_json=self._assert_public_settings(metadata),
        )
        try:
            async with session.begin_nested():
                session.add(quote)
                await session.flush()
        except IntegrityError:
            existing = (
                await session.execute(
                    select(PaymentQuote).where(
                        PaymentQuote.tenant_id == tenant_id,
                        PaymentQuote.quote_fingerprint == fingerprint,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing
            raise
        return quote

    async def _latest_live_quote(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        intent_id: uuid.UUID,
    ) -> PaymentQuote | None:
        now = datetime.now(UTC)
        stmt = (
            select(PaymentQuote)
            .where(
                PaymentQuote.tenant_id == tenant_id,
                PaymentQuote.payment_intent_id == intent_id,
                PaymentQuote.expires_at >= now,
            )
            .order_by(PaymentQuote.created_at.desc(), PaymentQuote.id.desc())
            .limit(1)
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    @staticmethod
    def _fingerprint(
        *,
        tenant_id: uuid.UUID,
        intent_id: uuid.UUID,
        method_id: uuid.UUID,
        source: PaymentObservationSource,
        external_reference: str | None,
        asset: str | None,
        network: str | None,
        destination_address: str | None,
        asset_amount: Decimal | None,
    ) -> str:
        payload = {
            "tenant_id": str(tenant_id),
            "intent_id": str(intent_id),
            "method_id": str(method_id),
            "source": source.value,
            "external_reference": (external_reference or "").strip(),
            "asset": asset,
            "network": network,
            "destination_address": (destination_address or "").strip(),
            "asset_amount": format(asset_amount, "f") if asset_amount is not None else None,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    async def submit_observation(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        intent_id: uuid.UUID,
        source: PaymentObservationSource,
        external_reference: str | None = None,
        asset_amount: Decimal | None = None,
        details: dict[str, Any] | None = None,
    ) -> PaymentObservation:
        intent = await self.payment_service.get_payment_intent(session, tenant_id, intent_id)
        if intent.user_id != user_id or intent.purpose != PaymentIntentPurpose.WALLET_TOPUP:
            raise PaymentError("Payment intent is not an accessible wallet top-up.")
        if intent.payment_method_id is None:
            raise PaymentError("Payment intent is not associated with a Phase 11 payment method.")
        if intent.status in {
            PaymentIntentStatus.FAILED,
            PaymentIntentStatus.EXPIRED,
            PaymentIntentStatus.CANCELLED,
        }:
            raise PaymentError(f"Payment intent is terminal in status {intent.status.value}.")
        method = await self.get_method(
            session, tenant_id=tenant_id, method_id=intent.payment_method_id
        )

        if source == PaymentObservationSource.ONCHAIN:
            if method.verification_mode not in {
                PaymentVerificationMode.ONCHAIN,
                PaymentVerificationMode.HYBRID,
            }:
                raise PaymentError("This payment method does not accept on-chain evidence.")
            if not (external_reference or "").strip():
                raise PaymentError("On-chain evidence requires a transaction hash/reference.")
        elif source in {PaymentObservationSource.MANUAL, PaymentObservationSource.EXCHANGE}:
            if method.verification_mode not in {
                PaymentVerificationMode.MANUAL,
                PaymentVerificationMode.HYBRID,
            }:
                raise PaymentError("This payment method does not accept manual evidence.")
        else:
            raise PaymentError("Customer-submitted provider observations are not authoritative.")

        normalized_asset_amount: Decimal | None = None
        if asset_amount is not None:
            try:
                normalized_asset_amount = Decimal(asset_amount)
            except (InvalidOperation, TypeError) as exc:
                raise PaymentError("Submitted asset amount is invalid.") from exc
            if normalized_asset_amount <= 0:
                raise PaymentError("Submitted asset amount must be positive.")

        asset = self._normalize_asset(method.asset)
        network = self._normalize_network(method.network)
        ref = (external_reference or "").strip() or None
        if ref is not None and source == PaymentObservationSource.ONCHAIN:
            ref = self._normalize_onchain_reference(ref)
        fingerprint = self._fingerprint(
            tenant_id=tenant_id,
            intent_id=intent.id,
            method_id=method.id,
            source=source,
            external_reference=ref,
            asset=asset,
            network=network,
            destination_address=method.destination_address,
            asset_amount=normalized_asset_amount,
        )
        existing_stmt = select(PaymentObservation).where(
            PaymentObservation.tenant_id == tenant_id,
            PaymentObservation.evidence_fingerprint == fingerprint,
        )
        existing = (await session.execute(existing_stmt)).scalar_one_or_none()
        if existing is not None:
            if existing.payment_intent_id != intent.id:
                raise PaymentIntegrityError("Payment evidence fingerprint is already bound elsewhere.")
            return existing

        quote = (
            await self._latest_live_quote(session, tenant_id=tenant_id, intent_id=intent.id)
            if source == PaymentObservationSource.ONCHAIN
            else None
        )
        status = (
            PaymentObservationStatus.PENDING_VERIFICATION
            if source == PaymentObservationSource.ONCHAIN
            else PaymentObservationStatus.MANUAL_REVIEW
        )
        observation = PaymentObservation(
            tenant_id=tenant_id,
            payment_intent_id=intent.id,
            payment_method_id=method.id,
            payment_quote_id=quote.id if quote is not None else None,
            source=source,
            status=status,
            external_reference=ref,
            asset=asset,
            network=network,
            destination_address=method.destination_address,
            asset_amount=normalized_asset_amount,
            evidence_fingerprint=fingerprint,
            observed_at=datetime.now(UTC),
            details_json=self._assert_public_settings(details),
        )
        try:
            async with session.begin_nested():
                session.add(observation)
                await session.flush()
        except IntegrityError as exc:
            existing = (await session.execute(existing_stmt)).scalar_one_or_none()
            if existing is not None:
                return existing
            raise PaymentIntegrityError(
                "This on-chain transaction/reference has already been used by another payment observation."
            ) from exc
        return observation

    async def get_observation(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        observation_id: uuid.UUID,
        for_update: bool = False,
    ) -> PaymentObservation:
        stmt = select(PaymentObservation).where(
            PaymentObservation.id == observation_id,
            PaymentObservation.tenant_id == tenant_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        observation = (await session.execute(stmt)).scalar_one_or_none()
        if observation is None:
            raise PaymentError("Payment observation not found.")
        return observation

    async def approve_manual_observation(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        observation_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        approved_amount: Decimal | None = None,
        approved_currency: str | None = None,
    ) -> PaymentObservation:
        observation = await self.get_observation(
            session,
            tenant_id=tenant_id,
            observation_id=observation_id,
            for_update=True,
        )
        if observation.status == PaymentObservationStatus.VERIFIED:
            await self.payment_service.settle_payment_intent(
                session,
                tenant_id,
                observation.payment_intent_id,
                verified_amount=observation.settlement_amount,
                verified_currency=observation.settlement_currency,
            )
            return observation
        if observation.status == PaymentObservationStatus.REJECTED:
            raise PaymentIntegrityError("Rejected payment evidence cannot be approved.")
        if observation.source not in {
            PaymentObservationSource.MANUAL,
            PaymentObservationSource.EXCHANGE,
            PaymentObservationSource.ONCHAIN,
        }:
            raise PaymentIntegrityError("This payment evidence cannot be manually approved.")

        intent = await self.payment_service.get_payment_intent(
            session, tenant_id, observation.payment_intent_id
        )
        amount = approved_amount if approved_amount is not None else intent.amount
        currency = (approved_currency or intent.currency).strip().upper()
        if amount != intent.amount or currency != intent.currency:
            raise PaymentIntegrityError(
                "Manual approval cannot change the authoritative settlement amount/currency. "
                "Resolve under/over-payments through a dedicated financial review flow."
            )

        observation.settlement_amount = intent.amount
        observation.settlement_currency = intent.currency
        observation.assurance_level = PaymentAssuranceLevel.MANUAL_APPROVED
        observation.status = PaymentObservationStatus.VERIFIED
        observation.is_final = True
        observation.verified_at = datetime.now(UTC)
        observation.verified_by_user_id = actor_user_id
        observation.rejection_reason = None
        await self.payment_service.settle_payment_intent(
            session,
            tenant_id,
            intent.id,
            verified_amount=intent.amount,
            verified_currency=intent.currency,
        )
        await session.flush()
        return observation

    async def reject_observation(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        observation_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        reason: str,
    ) -> PaymentObservation:
        observation = await self.get_observation(
            session, tenant_id=tenant_id, observation_id=observation_id, for_update=True
        )
        if observation.status == PaymentObservationStatus.VERIFIED:
            raise PaymentIntegrityError("Verified payment evidence cannot be rejected or reversed in place.")
        if observation.status == PaymentObservationStatus.REJECTED:
            return observation
        normalized_reason = reason.strip()
        if not normalized_reason or len(normalized_reason) > 500:
            raise PaymentError("A rejection reason of 1-500 characters is required.")
        observation.status = PaymentObservationStatus.REJECTED
        observation.rejection_reason = normalized_reason
        observation.verified_by_user_id = actor_user_id
        observation.verified_at = datetime.now(UTC)
        observation.is_final = True
        await session.flush()
        return observation

    async def verify_onchain_observation(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        observation_id: uuid.UUID,
    ) -> PaymentObservation:
        observation = await self.get_observation(
            session, tenant_id=tenant_id, observation_id=observation_id, for_update=True
        )
        if observation.source != PaymentObservationSource.ONCHAIN:
            raise PaymentError("Only on-chain observations can be verified with a chain verifier.")
        if observation.status == PaymentObservationStatus.VERIFIED:
            return observation
        if observation.status == PaymentObservationStatus.REJECTED:
            raise PaymentIntegrityError("Rejected payment evidence cannot be re-verified in place.")
        method = await self.get_method(
            session, tenant_id=tenant_id, method_id=observation.payment_method_id
        )
        if not observation.external_reference or not method.network:
            raise PaymentIntegrityError("On-chain observation is missing network/transaction identity.")

        verifier = self.onchain_registry.get(method.network)
        result = await verifier.verify_transaction(
            method.network,
            observation.external_reference,
            expected_asset=method.asset,
            expected_destination=method.destination_address,
        )
        expected_network = self._normalize_network(method.network)
        expected_asset = self._normalize_asset(method.asset)
        if self._normalize_network(result.network) != expected_network:
            raise PaymentIntegrityError("Verifier returned a different blockchain network.")
        if self._normalize_asset(result.asset) != expected_asset:
            raise PaymentIntegrityError("Verifier returned a different payment asset/token.")
        if result.tx_hash.strip().lower() != observation.external_reference.strip().lower():
            raise PaymentIntegrityError("Verifier returned a different transaction reference.")
        if (method.destination_address or "").strip() != result.destination_address.strip():
            raise PaymentIntegrityError("Transaction destination does not match the payment method address.")
        if not result.succeeded:
            observation.status = PaymentObservationStatus.REJECTED
            observation.rejection_reason = "Authoritative chain verifier reports transaction failure."
            observation.is_final = bool(result.is_final)
            observation.confirmations = max(0, result.confirmations)
            observation.observed_at = result.observed_at or datetime.now(UTC)
            await session.flush()
            return observation

        observation.asset_amount = result.asset_amount
        observation.confirmations = max(0, result.confirmations)
        observation.is_final = bool(result.is_final)
        observation.observed_at = result.observed_at or datetime.now(UTC)
        observation.details_json = {
            **(observation.details_json or {}),
            "verifier": getattr(verifier, "verifier_name", verifier.__class__.__name__),
            "verification_summary": {
                "network": result.network,
                "asset": result.asset,
                "confirmations": result.confirmations,
                "is_final": result.is_final,
                "succeeded": result.succeeded,
            },
        }
        if not result.is_final:
            observation.status = PaymentObservationStatus.PENDING_VERIFICATION
            await session.flush()
            return observation

        intent = await self.payment_service.get_payment_intent(
            session, tenant_id, observation.payment_intent_id
        )
        quote = None
        if observation.payment_quote_id is not None:
            quote = await session.get(PaymentQuote, observation.payment_quote_id)
            if quote is not None and quote.tenant_id != tenant_id:
                raise PaymentIntegrityError("Payment quote tenant mismatch.")
        if quote is None:
            # Deliberately no implicit USDT/USD parity or client-supplied FX authority.
            observation.status = PaymentObservationStatus.MANUAL_REVIEW
            observation.assurance_level = PaymentAssuranceLevel.ONCHAIN_VERIFIED
            await session.flush()
            return observation
        if result.observed_at is not None and result.observed_at.tzinfo is None:
            raise PaymentIntegrityError("Verifier observed_at must be timezone-aware.")
        effective_observed_at = result.observed_at or datetime.now(UTC)
        if self._as_utc(effective_observed_at) > self._as_utc(quote.expires_at):
            observation.status = PaymentObservationStatus.MANUAL_REVIEW
            observation.assurance_level = PaymentAssuranceLevel.ONCHAIN_VERIFIED
            observation.rejection_reason = "Transaction occurred after the authoritative payment quote expired."
            await session.flush()
            return observation
        if (
            quote.payment_intent_id != intent.id
            or quote.payment_method_id != method.id
            or quote.settlement_amount != intent.amount
            or quote.settlement_currency != intent.currency
            or self._normalize_asset(quote.asset) != expected_asset
            or self._normalize_network(quote.network) != expected_network
            or result.asset_amount != quote.asset_amount
        ):
            observation.status = PaymentObservationStatus.MANUAL_REVIEW
            observation.assurance_level = PaymentAssuranceLevel.ONCHAIN_VERIFIED
            observation.settlement_amount = quote.settlement_amount
            observation.settlement_currency = quote.settlement_currency
            observation.rejection_reason = "Verified chain payment does not exactly match the authoritative quote."
            await session.flush()
            return observation

        observation.status = PaymentObservationStatus.VERIFIED
        observation.assurance_level = PaymentAssuranceLevel.ONCHAIN_VERIFIED
        observation.settlement_amount = quote.settlement_amount
        observation.settlement_currency = quote.settlement_currency
        observation.verified_at = datetime.now(UTC)
        await self.payment_service.settle_payment_intent(
            session,
            tenant_id,
            intent.id,
            verified_amount=quote.settlement_amount,
            verified_currency=quote.settlement_currency,
        )
        await session.flush()
        return observation
