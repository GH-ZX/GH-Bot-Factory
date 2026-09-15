import os
from typing import Protocol, runtime_checkable

from packages.core.exceptions import BotFactoryError


class SecretNotFoundError(BotFactoryError):
    """Raised when a requested secret reference cannot be found."""


def mask_token(token: str) -> str:
    """Safely mask a Telegram token for logging or diagnostics.
    
    Example: '123456789:ABCdefGHIjk' -> '123456789:***...GHIjk'
    """
    if not token or ":" not in token:
        return "***"
    bot_id, secret_part = token.split(":", 1)
    if len(secret_part) <= 6:
        return f"{bot_id}:***"
    return f"{bot_id}:***...{secret_part[-4:]}"


@runtime_checkable
class SecretStorage(Protocol):
    """Protocol for abstracting secret retrieval from env or external vaults."""

    async def get_secret(self, secret_ref: str) -> str:
        """Retrieve a secret by reference or key."""
        ...

    async def set_secret(self, secret_ref: str, secret_value: str) -> None:
        """Store or update a secret by reference or key."""
        ...


class EnvSecretStorage:
    """Secret storage backed by environment variables and in-memory overrides.
    
    Suitable for development and staging environments.
    """

    def __init__(self, in_memory_store: dict[str, str] | None = None) -> None:
        self._memory_store = in_memory_store if in_memory_store is not None else {}

    async def get_secret(self, secret_ref: str) -> str:
        if secret_ref in self._memory_store:
            return self._memory_store[secret_ref]
        val = os.getenv(secret_ref)
        if val is not None:
            return val
        raise SecretNotFoundError(f"Secret reference '{secret_ref}' not found in environment or memory.")

    async def set_secret(self, secret_ref: str, secret_value: str) -> None:
        self._memory_store[secret_ref] = secret_value
