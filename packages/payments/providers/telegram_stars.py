import asyncio
import json
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any
from urllib import error, request

from packages.payments.exceptions import PaymentProviderError, UnsupportedProviderCapabilityError
from packages.payments.providers.interface import (
    PaymentCreateRequest,
    PaymentCreateResult,
    PaymentDetailsResult,
    PaymentRefundRequest,
    PaymentRefundResult,
    WebhookVerificationResult,
)
from packages.payments.state_machine import PaymentIntentStatus

ApiCaller = Callable[[str, dict[str, Any]], Awaitable[Any]]


class TelegramStarsProvider:
    """Telegram Stars adapter backed directly by the Bot API.

    The bot token is supplied by SecretStorage through the provider registry and is never
    persisted in payment records. Stars invoice links are native Telegram invoices; final
    settlement is authoritative only after a successful_payment update is validated.
    """

    provider_name = "telegram_stars"
    supports_idempotency_keys = True
    supports_payment_lookup = True
    supports_webhooks = False
    supports_refunds = True
    supports_partial_refunds = False
    supports_safe_refund_retries = True

    def __init__(
        self,
        settings: dict[str, Any],
        bot_token: str,
        webhook_secret: str | None = None,
        api_call: ApiCaller | None = None,
    ) -> None:
        del webhook_secret
        if not bot_token or ":" not in bot_token:
            raise PaymentProviderError("Telegram Stars bot token is not configured correctly.")
        self.settings = settings or {}
        self._bot_token = bot_token
        self._api_base = str(self.settings.get("api_base_url") or "https://api.telegram.org").rstrip("/")
        self._api_call_override = api_call
        self._transaction_scan_pages = max(1, min(int(self.settings.get("transaction_scan_pages", 10)), 50))

    @staticmethod
    def _whole_stars(amount: Decimal) -> int:
        if amount <= Decimal("0") or amount != amount.to_integral_value():
            raise PaymentProviderError("Telegram Stars payments must use a positive whole XTR amount.")
        return int(amount)

    async def _call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        payload = payload or {}
        if self._api_call_override is not None:
            return await self._api_call_override(method, payload)

        url = f"{self._api_base}/bot{self._bot_token}/{method}"
        body = json.dumps(payload).encode("utf-8")

        def do_request() -> Any:
            req = request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with request.urlopen(req, timeout=20) as response:  # noqa: S310
                    raw = response.read()
            except error.HTTPError as exc:
                raw = exc.read()
            except (error.URLError, TimeoutError, OSError) as exc:
                raise PaymentProviderError("Telegram Bot API request failed.") from exc

            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise PaymentProviderError("Telegram Bot API returned an invalid response.") from exc
            if not parsed.get("ok"):
                description = str(parsed.get("description") or "Telegram Bot API rejected the request.")
                error_code = parsed.get("error_code")
                exc = PaymentProviderError(description)
                setattr(exc, "provider_error_code", error_code)
                setattr(exc, "provider_error_description", description)
                raise exc
            return parsed.get("result")

        return await asyncio.to_thread(do_request)

    @staticmethod
    def _invoice_payload(request_data: PaymentCreateRequest) -> str:
        intent_id = str(request_data.metadata.get("payment_intent_id") or "").strip()
        if not intent_id:
            raise PaymentProviderError("Telegram Stars invoice is missing payment_intent_id metadata.")
        return f"ghbf:wallet-topup:{intent_id}"

    async def create_payment(self, request_data: PaymentCreateRequest) -> PaymentCreateResult:
        if request_data.currency.upper() != "XTR":
            raise PaymentProviderError("Telegram Stars only supports XTR currency.")
        amount = self._whole_stars(request_data.amount)
        invoice_payload = self._invoice_payload(request_data)
        title = str(self.settings.get("invoice_title") or "Wallet top-up")[:32]
        description = str(
            self.settings.get("invoice_description")
            or "Add Telegram Stars to your store wallet."
        )[:255]
        label = str(self.settings.get("price_label") or "Wallet credit")[:32]

        invoice_url = await self._call(
            "createInvoiceLink",
            {
                "title": title,
                "description": description,
                "payload": invoice_payload,
                "currency": "XTR",
                "prices": [{"label": label, "amount": amount}],
            },
        )
        if not isinstance(invoice_url, str) or not invoice_url.startswith("https://"):
            raise PaymentProviderError("Telegram did not return a valid invoice link.")
        return PaymentCreateResult(
            provider_payment_id=f"stars_invoice:{request_data.metadata['payment_intent_id']}",
            status=PaymentIntentStatus.PENDING,
            checkout_url=invoice_url,
            raw_data={"invoice_payload": invoice_payload},
        )

    async def list_recent_star_transactions(self) -> list[dict[str, Any]]:
        """Return the configured recent Telegram Stars transaction window.

        The Bot API does not expose a webhook specifically for external chargebacks.
        Reconciliation therefore polls the authenticated transaction history and relies
        on durable database event deduplication.
        """
        transactions: list[dict[str, Any]] = []
        for page in range(self._transaction_scan_pages):
            result = await self._call(
                "getStarTransactions",
                {"offset": page * 100, "limit": 100},
            )
            page_transactions = result.get("transactions", []) if isinstance(result, dict) else []
            transactions.extend(
                transaction for transaction in page_transactions if isinstance(transaction, dict)
            )
            if len(page_transactions) < 100:
                break
        return transactions

    async def _find_star_transaction(self, transaction_id: str) -> dict[str, Any] | None:
        for page in range(self._transaction_scan_pages):
            result = await self._call(
                "getStarTransactions",
                {"offset": page * 100, "limit": 100},
            )
            transactions = result.get("transactions", []) if isinstance(result, dict) else []
            for transaction in transactions:
                if str(transaction.get("id")) == transaction_id:
                    return transaction
            if len(transactions) < 100:
                break
        return None

    async def get_payment(self, provider_payment_id: str) -> PaymentDetailsResult:
        if provider_payment_id.startswith("stars_invoice:"):
            return PaymentDetailsResult(
                provider_payment_id=provider_payment_id,
                status=PaymentIntentStatus.PENDING,
                amount=Decimal("0"),
                currency="XTR",
                raw_data={"invoice_pending": True},
            )

        transaction = await self._find_star_transaction(provider_payment_id)
        if transaction is None:
            return PaymentDetailsResult(
                provider_payment_id=provider_payment_id,
                status=PaymentIntentStatus.UNKNOWN,
                amount=Decimal("0"),
                currency="XTR",
                raw_data={"transaction_found": False},
            )

        amount = Decimal(str(abs(int(transaction.get("amount", 0)))))
        if transaction.get("source") is not None:
            status = PaymentIntentStatus.SUCCEEDED
        elif transaction.get("receiver") is not None:
            # Refund transactions share the original transaction id. The original
            # PaymentIntent remains historically SUCCEEDED; the reversal saga tracks refund state.
            status = PaymentIntentStatus.SUCCEEDED
        else:
            status = PaymentIntentStatus.UNKNOWN
        return PaymentDetailsResult(
            provider_payment_id=provider_payment_id,
            status=status,
            amount=amount,
            currency="XTR",
            raw_data=transaction,
        )

    async def verify_webhook(
        self,
        payload: bytes | str,
        headers: dict[str, str],
        secret: str,
    ) -> WebhookVerificationResult:
        del payload, headers, secret
        raise UnsupportedProviderCapabilityError(
            "Telegram Stars settlement is delivered through authenticated bot updates, not payment webhooks."
        )

    async def refund(self, request_data: PaymentRefundRequest) -> PaymentRefundResult:
        if request_data.currency.upper() != "XTR":
            raise PaymentProviderError("Telegram Stars refunds require XTR currency.")
        amount = self._whole_stars(request_data.amount)
        telegram_user_id = request_data.metadata.get("telegram_user_id")
        if not isinstance(telegram_user_id, int) or telegram_user_id <= 0:
            raise PaymentProviderError("Telegram Stars refund is missing the Telegram user identifier.")
        charge_id = request_data.provider_payment_id.strip()
        if not charge_id or charge_id.startswith("stars_invoice:"):
            raise PaymentProviderError("Telegram Stars refund requires a settled payment charge id.")

        try:
            result = await self._call(
                "refundStarPayment",
                {
                    "user_id": telegram_user_id,
                    "telegram_payment_charge_id": charge_id,
                },
            )
            if result is not True:
                raise PaymentProviderError("Telegram did not confirm the Stars refund.")
        except PaymentProviderError as exc:
            description = str(getattr(exc, "provider_error_description", str(exc))).upper()
            # Telegram/MTProto documents CHARGE_ALREADY_REFUNDED. Treating it as
            # idempotent success closes the crash window between provider success and DB commit.
            if "CHARGE_ALREADY_REFUNDED" not in description and "ALREADY REFUNDED" not in description:
                raise

        return PaymentRefundResult(
            provider_refund_id=charge_id,
            status="SUCCEEDED",
            amount=Decimal(amount),
            is_success=True,
            raw_data={"telegram_payment_charge_id": charge_id},
        )
