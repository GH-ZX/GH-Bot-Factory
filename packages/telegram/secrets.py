from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol, runtime_checkable

from cryptography.fernet import Fernet, InvalidToken

from packages.core.config import settings
from packages.core.exceptions import BotFactoryError

try:  # Linux containers; tests on non-POSIX still work without process locking.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


class SecretNotFoundError(BotFactoryError):
    """Raised when a requested secret reference cannot be found."""


class SecretStorageError(BotFactoryError):
    """Raised when the local secret vault cannot be read or written safely."""


_SECRET_REF_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{1,254}$")


def mask_token(token: str) -> str:
    """Safely mask a Telegram token for logging or diagnostics."""
    if not token or ":" not in token:
        return "***"
    bot_id, secret_part = token.split(":", 1)
    if len(secret_part) <= 6:
        return f"{bot_id}:***"
    return f"{bot_id}:***...{secret_part[-4:]}"


def _validate_ref(secret_ref: str) -> str:
    ref = secret_ref.strip()
    if not _SECRET_REF_RE.fullmatch(ref):
        raise SecretStorageError("Secret reference must be an environment-style identifier.")
    return ref


@runtime_checkable
class SecretStorage(Protocol):
    async def get_secret(self, secret_ref: str) -> str:
        """Retrieve a secret by reference or key."""
        ...

    async def set_secret(self, secret_ref: str, secret_value: str) -> None:
        """Store or update a secret by reference or key."""
        ...

    async def delete_secret(self, secret_ref: str) -> None:
        """Delete a secret when supported."""
        ...


class EnvSecretStorage:
    """Environment-backed storage with optional in-memory test overrides."""

    def __init__(self, in_memory_store: dict[str, str] | None = None) -> None:
        self._memory_store = in_memory_store if in_memory_store is not None else {}

    async def get_secret(self, secret_ref: str) -> str:
        ref = _validate_ref(secret_ref)
        if ref in self._memory_store:
            return self._memory_store[ref]
        val = os.getenv(ref)
        if val is not None:
            return val
        raise SecretNotFoundError(f"Secret reference '{ref}' not found in environment or memory.")

    async def set_secret(self, secret_ref: str, secret_value: str) -> None:
        self._memory_store[_validate_ref(secret_ref)] = secret_value

    async def delete_secret(self, secret_ref: str) -> None:
        self._memory_store.pop(_validate_ref(secret_ref), None)


class EncryptedFileSecretStorage:
    """Small self-hosted encrypted vault shared by API, worker, and bot runtime.

    The ciphertext database and a restrictive master-key file live in the configured
    persistent Docker volume. This is intended for self-hosted/single-node operation.
    External KMS/Vault integrations can implement the same SecretStorage protocol.
    """

    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = Path(directory or settings.local_secret_vault_dir)
        self.key_path = self.directory / "master.key"
        self.vault_path = self.directory / "secrets.json"
        self.lock_path = self.directory / ".lock"

    def _ensure_directory(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass

    def _load_or_create_key(self) -> bytes:
        self._ensure_directory()
        if self.key_path.exists():
            key = self.key_path.read_bytes().strip()
            if not key:
                raise SecretStorageError("Local vault master key is empty.")
            return key

        generated = Fernet.generate_key()
        try:
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return self.key_path.read_bytes().strip()
        with os.fdopen(fd, "wb") as handle:
            handle.write(generated + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        return generated

    def _fernet(self) -> Fernet:
        try:
            return Fernet(self._load_or_create_key())
        except (ValueError, TypeError) as exc:
            raise SecretStorageError("Local vault master key is invalid.") from exc

    def _locked(self, exclusive: bool):
        self._ensure_directory()
        handle = open(self.lock_path, "a+b")
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        return handle

    def _unlock(self, handle) -> None:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

    def _read_payload(self) -> dict[str, str]:
        if not self.vault_path.exists():
            return {}
        try:
            raw = json.loads(self.vault_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SecretStorageError("Local vault file is unreadable or corrupt.") from exc
        if not isinstance(raw, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in raw.items()):
            raise SecretStorageError("Local vault file has an invalid structure.")
        return raw

    def _write_payload(self, payload: dict[str, str]) -> None:
        self._ensure_directory()
        fd, temp_name = tempfile.mkstemp(prefix=".secrets-", suffix=".tmp", dir=self.directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.vault_path)
            try:
                os.chmod(self.vault_path, 0o600)
            except OSError:
                pass
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    async def get_secret(self, secret_ref: str) -> str:
        ref = _validate_ref(secret_ref)
        handle = self._locked(exclusive=False)
        try:
            payload = self._read_payload()
            encrypted = payload.get(ref)
        finally:
            self._unlock(handle)
        if encrypted is None:
            raise SecretNotFoundError(f"Secret reference '{ref}' not found in local vault.")
        try:
            return self._fernet().decrypt(encrypted.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise SecretStorageError(f"Secret reference '{ref}' cannot be decrypted.") from exc

    async def set_secret(self, secret_ref: str, secret_value: str) -> None:
        ref = _validate_ref(secret_ref)
        if not secret_value:
            raise SecretStorageError("Refusing to store an empty secret.")
        encrypted = self._fernet().encrypt(secret_value.encode("utf-8")).decode("ascii")
        handle = self._locked(exclusive=True)
        try:
            payload = self._read_payload()
            payload[ref] = encrypted
            self._write_payload(payload)
        finally:
            self._unlock(handle)

    async def delete_secret(self, secret_ref: str) -> None:
        ref = _validate_ref(secret_ref)
        handle = self._locked(exclusive=True)
        try:
            payload = self._read_payload()
            if ref in payload:
                del payload[ref]
                self._write_payload(payload)
        finally:
            self._unlock(handle)


class CompositeSecretStorage:
    """Resolve environment secrets first, then the local encrypted vault."""

    def __init__(
        self,
        env_storage: SecretStorage | None = None,
        vault_storage: SecretStorage | None = None,
    ) -> None:
        self.env_storage = env_storage or EnvSecretStorage()
        self.vault_storage = vault_storage or EncryptedFileSecretStorage()

    async def get_secret(self, secret_ref: str) -> str:
        try:
            return await self.env_storage.get_secret(secret_ref)
        except SecretNotFoundError:
            return await self.vault_storage.get_secret(secret_ref)

    async def set_secret(self, secret_ref: str, secret_value: str) -> None:
        await self.vault_storage.set_secret(secret_ref, secret_value)

    async def delete_secret(self, secret_ref: str) -> None:
        await self.vault_storage.delete_secret(secret_ref)


def get_default_secret_storage() -> SecretStorage:
    """Return the runtime secret boundary used by self-hosted services."""
    if settings.local_secret_vault_enabled:
        return CompositeSecretStorage()
    return EnvSecretStorage()
