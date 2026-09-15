"""Exception hierarchy for external service providers."""

from packages.core.exceptions import BotFactoryError


class ProviderError(BotFactoryError):
    """Base exception for all upstream provider interactions."""

    is_retryable: bool = False

    def __init__(self, message: str, is_retryable: bool | None = None) -> None:
        super().__init__(message)
        if is_retryable is not None:
            self.is_retryable = is_retryable


class ProviderAuthenticationError(ProviderError):
    """Raised when upstream API credentials or tokens are invalid or expired."""

    is_retryable = False


class ProviderRateLimitError(ProviderError):
    """Raised when upstream provider throttles requests."""

    is_retryable = True


class ProviderInsufficientBalanceError(ProviderError):
    """Raised when the tenant's account balance with the upstream provider is depleted."""

    is_retryable = True  # Can fall back to an alternate provider


class ProviderProductUnavailableError(ProviderError):
    """Raised when an external product is out of stock or disabled upstream."""

    is_retryable = True  # Can fall back to an alternate provider


class ProviderTimeoutError(ProviderError):
    """Raised when upstream network connection times out."""

    is_retryable = True


class ProviderOrderFailedError(ProviderError):
    """Raised when the upstream order placement fails explicitly."""

    def __init__(self, message: str, is_retryable: bool = False) -> None:
        super().__init__(message, is_retryable=is_retryable)
