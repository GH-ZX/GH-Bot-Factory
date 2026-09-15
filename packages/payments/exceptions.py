"""Domain exceptions for payment infrastructure and multi-client authentication."""

from packages.core.exceptions import BotFactoryError


class PaymentError(BotFactoryError):
    """Base exception for all payment-related errors."""


class PaymentIntentNotFoundError(PaymentError):
    """Raised when a payment intent cannot be found within tenant scope."""


class PaymentIntegrityError(PaymentError):
    """Raised when payment verification detects amount, currency, or order discrepancies."""


class PaymentProviderError(PaymentError):
    """Raised when an upstream payment provider operation fails or returns an error."""


class UnsupportedProviderCapabilityError(PaymentProviderError):
    """Raised when an operation is requested that the provider does not support."""


class WebhookVerificationError(PaymentError):
    """Raised when webhook signature, timestamp, or authenticity verification fails."""


class WebhookDuplicateEventError(PaymentError):
    """Raised when a duplicate webhook event is rejected."""


class MiniAppAuthError(BotFactoryError):
    """Base exception for Telegram Mini App authentication errors."""


class MiniAppSignatureInvalidError(MiniAppAuthError):
    """Raised when Telegram initData signature/hash is invalid."""


class MiniAppExpiredError(MiniAppAuthError):
    """Raised when Telegram initData auth_date has expired."""


class MiniAppDataTamperedError(MiniAppAuthError):
    """Raised when Telegram initData structure or user JSON is corrupted or tampered."""
