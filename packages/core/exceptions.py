"""Domain exceptions for GH-Bot-Factory."""


class BotFactoryError(Exception):
    """Base exception for all domain errors."""


class TenantAccessViolationError(BotFactoryError):
    """Raised when an operation attempts unauthorized cross-tenant data access."""


class InvalidStateTransitionError(BotFactoryError):
    """Raised when an illegal order or entity state transition is attempted."""


class InsufficientFundsError(BotFactoryError):
    """Raised when a wallet debit exceeds available balance."""


class LedgerIntegrityError(BotFactoryError):
    """Raised when ledger verification or reconstruction detects a discrepancy."""


class ProviderError(BotFactoryError):
    """Raised when an upstream provider request fails."""
