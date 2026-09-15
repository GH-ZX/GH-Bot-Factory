from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.database import async_session_factory
from packages.factory.models import BotProvisioningJob, BotProvisioningStatus
from packages.telegram.models import Bot
from packages.telegram.secrets import SecretNotFoundError, SecretStorage, get_default_secret_storage
from packages.tenants.models import AuditLog, Tenant

_SECRET_REF_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{1,254}$")
_TELEGRAM_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")
_SECRETISH = ("secret", "password", "token", "api_key", "apikey", "authorization", "credential")


class ProvisioningError(Exception):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class VerifiedTelegramBot:
    telegram_bot_id: int
    username: str | None
    display_name: str


class TelegramIdentityVerifier(Protocol):
    async def verify(self, token: str) -> VerifiedTelegramBot: ...


class AiogramTelegramIdentityVerifier:
    """Resolve Telegram bot identity without exposing token material to persistence/logging."""

    async def verify(self, token: str) -> VerifiedTelegramBot:
        from aiogram import Bot as AiogramBot
        from aiogram.exceptions import (
            TelegramNetworkError,
            TelegramRetryAfter,
            TelegramServerError,
            TelegramUnauthorizedError,
        )

        bot = AiogramBot(token=token)
        try:
            me = await bot.get_me()
            display_name = " ".join(part for part in [me.first_name, me.last_name] if part).strip()
            return VerifiedTelegramBot(
                telegram_bot_id=int(me.id),
                username=me.username,
                display_name=display_name or me.username or f"Telegram Bot {me.id}",
            )
        except TelegramUnauthorizedError as exc:
            raise ProvisioningError("TELEGRAM_TOKEN_UNAUTHORIZED", retryable=False) from exc
        except TelegramRetryAfter as exc:
            raise ProvisioningError("TELEGRAM_RATE_LIMITED", retryable=True) from exc
        except (TelegramNetworkError, TelegramServerError) as exc:
            raise ProvisioningError("TELEGRAM_TEMPORARY_FAILURE", retryable=True) from exc
        except ProvisioningError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalized at trust boundary
            raise ProvisioningError("TELEGRAM_VERIFICATION_FAILED", retryable=False) from exc
        finally:
            await bot.session.close()


def normalize_expected_username(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lstrip("@").lower()
    return normalized or None


def validate_secret_ref(value: str) -> str:
    ref = value.strip()
    if _TELEGRAM_TOKEN_RE.fullmatch(ref):
        raise ValueError("Telegram token material is forbidden; submit a secret reference only.")
    if not _SECRET_REF_RE.fullmatch(ref):
        raise ValueError("Secret reference must be an environment/vault key identifier, not secret material.")
    return ref


def reject_secret_material(value: Any, *, path: str = "config") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).lower()
            if any(fragment in normalized for fragment in _SECRETISH):
                raise ValueError(f"{path}.{key} looks like secret material; store only secret references.")
            reject_secret_material(nested, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, nested in enumerate(value):
            reject_secret_material(nested, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and _TELEGRAM_TOKEN_RE.fullmatch(value.strip()):
        raise ValueError(f"{path} contains Telegram token-shaped secret material.")


def provisioning_fingerprint(
    *,
    token_secret_ref: str,
    expected_username: str | None,
    requested_display_name: str | None,
    desired_enabled: bool,
    desired_config: dict[str, Any],
    credential_fingerprint: str | None = None,
) -> str:
    payload = {
        "token_secret_ref": token_secret_ref,
        "expected_username": normalize_expected_username(expected_username),
        "requested_display_name": requested_display_name.strip() if requested_display_name else None,
        "desired_enabled": desired_enabled,
        "desired_config": desired_config,
    }
    if credential_fingerprint is not None:
        payload["credential_fingerprint"] = credential_fingerprint
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class BotProvisioningService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        secret_storage: SecretStorage | None = None,
        verifier: TelegramIdentityVerifier | None = None,
        lease_seconds: int = 60,
        base_backoff_seconds: int = 5,
        max_backoff_seconds: int = 300,
    ) -> None:
        self.session_factory = session_factory or async_session_factory
        self.secret_storage = secret_storage or get_default_secret_storage()
        self.verifier = verifier or AiogramTelegramIdentityVerifier()
        self.lease_seconds = lease_seconds
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds

    async def recover_stale_jobs(self, session: AsyncSession) -> int:
        now = datetime.now(UTC)
        stmt = (
            update(BotProvisioningJob)
            .where(
                BotProvisioningJob.status == BotProvisioningStatus.RUNNING,
                BotProvisioningJob.lease_expires_at.is_not(None),
                BotProvisioningJob.lease_expires_at < now,
            )
            .values(
                status=BotProvisioningStatus.RETRY,
                next_attempt_at=now,
                locked_at=None,
                lease_expires_at=None,
                last_error_code="WORKER_LEASE_EXPIRED",
                last_error_type="LeaseExpired",
            )
        )
        result = await session.execute(stmt)
        await session.commit()
        return int(result.rowcount or 0)

    async def claim_next_job(self, session: AsyncSession) -> BotProvisioningJob | None:
        now = datetime.now(UTC)
        stmt = (
            select(BotProvisioningJob)
            .where(
                BotProvisioningJob.status.in_(
                    [BotProvisioningStatus.PENDING, BotProvisioningStatus.RETRY]
                ),
                or_(
                    BotProvisioningJob.next_attempt_at.is_(None),
                    BotProvisioningJob.next_attempt_at <= now,
                ),
            )
            .order_by(BotProvisioningJob.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        job = (await session.execute(stmt)).scalar_one_or_none()
        if job is None:
            return None
        job.status = BotProvisioningStatus.RUNNING
        job.attempt_count += 1
        job.locked_at = now
        job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
        job.next_attempt_at = None
        job.last_error_code = None
        job.last_error_type = None
        await session.commit()
        return job

    async def _audit(self, session: AsyncSession, job: BotProvisioningJob, action: str, details: dict[str, Any]) -> None:
        session.add(
            AuditLog(
                tenant_id=job.tenant_id,
                user_id=job.requested_by_user_id,
                action=action,
                resource_type="BOT_PROVISIONING_JOB",
                resource_id=str(job.id),
                details=details,
            )
        )

    async def _mark_failure(self, job_id: uuid.UUID, exc: Exception) -> None:
        async with self.session_factory() as session:
            job = await session.get(BotProvisioningJob, job_id, with_for_update=True)
            if job is None or job.status != BotProvisioningStatus.RUNNING:
                return
            retryable = isinstance(exc, ProvisioningError) and exc.retryable
            code = exc.code if isinstance(exc, ProvisioningError) else "PROVISIONING_INTERNAL_ERROR"
            error_type = type(exc).__name__
            now = datetime.now(UTC)
            if retryable and job.attempt_count < job.max_attempts:
                delay = min(
                    self.max_backoff_seconds,
                    self.base_backoff_seconds * (2 ** max(job.attempt_count - 1, 0)),
                )
                job.status = BotProvisioningStatus.RETRY
                job.next_attempt_at = now + timedelta(seconds=delay)
                job.last_error_code = code
                job.last_error_type = error_type
                job.locked_at = None
                job.lease_expires_at = None
                await session.commit()
                return

            job.status = BotProvisioningStatus.FAILED
            job.completed_at = now
            job.last_error_code = code
            job.last_error_type = error_type
            job.locked_at = None
            job.lease_expires_at = None
            await self._audit(
                session,
                job,
                "BOT_PROVISIONING_FAILED",
                {"error_code": code, "attempt_count": job.attempt_count},
            )
            await session.commit()

    async def process_job(self, job: BotProvisioningJob) -> bool:
        try:
            try:
                token = await self.secret_storage.get_secret(job.token_secret_ref)
            except SecretNotFoundError as exc:
                raise ProvisioningError("TOKEN_SECRET_NOT_FOUND", retryable=False) from exc
            identity = await self.verifier.verify(token)
            expected = normalize_expected_username(job.expected_username)
            actual = normalize_expected_username(identity.username)
            if expected and expected != actual:
                raise ProvisioningError("TELEGRAM_USERNAME_MISMATCH", retryable=False)

            async with self.session_factory() as session:
                current = await session.get(BotProvisioningJob, job.id, with_for_update=True)
                if current is None or current.status != BotProvisioningStatus.RUNNING:
                    return False
                tenant = await session.get(Tenant, current.tenant_id)
                if tenant is None or not tenant.is_active or tenant.deleted_at is not None:
                    raise ProvisioningError("TENANT_INACTIVE", retryable=False)

                existing = (
                    await session.execute(
                        select(Bot)
                        .where(Bot.telegram_bot_id == identity.telegram_bot_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if existing is not None and existing.tenant_id != current.tenant_id:
                    raise ProvisioningError("TELEGRAM_BOT_OWNED_BY_ANOTHER_TENANT", retryable=False)

                if existing is None:
                    existing = Bot(
                        tenant_id=current.tenant_id,
                        telegram_bot_id=identity.telegram_bot_id,
                        username=identity.username,
                        display_name=current.requested_display_name or identity.display_name,
                        token_secret_ref=current.token_secret_ref,
                        credential_status="VERIFIED",
                        credential_verified_at=datetime.now(UTC),
                        is_enabled=current.desired_enabled,
                        config=current.desired_config,
                    )
                    session.add(existing)
                    try:
                        await session.flush()
                    except IntegrityError as exc:
                        raise ProvisioningError("TELEGRAM_IDENTITY_RACE", retryable=True) from exc
                else:
                    previous_ref = existing.token_secret_ref
                    existing.username = identity.username
                    existing.display_name = current.requested_display_name or identity.display_name
                    existing.token_secret_ref = current.token_secret_ref
                    existing.credential_status = "VERIFIED"
                    existing.credential_verified_at = datetime.now(UTC)
                    existing.credential_last_error_type = None
                    if previous_ref != current.token_secret_ref:
                        existing.credential_version += 1
                        existing.credential_rotated_at = datetime.now(UTC)
                    existing.is_enabled = current.desired_enabled
                    existing.config = current.desired_config
                    existing.deleted_at = None
                    await session.flush()

                current.bot_id = existing.id
                current.verified_telegram_bot_id = identity.telegram_bot_id
                current.verified_username = identity.username
                current.status = BotProvisioningStatus.READY
                current.completed_at = datetime.now(UTC)
                current.locked_at = None
                current.lease_expires_at = None
                current.last_error_code = None
                current.last_error_type = None
                await self._audit(
                    session,
                    current,
                    "BOT_PROVISIONING_READY",
                    {
                        "bot_id": str(existing.id),
                        "telegram_bot_id": identity.telegram_bot_id,
                        "username": identity.username,
                        "attempt_count": current.attempt_count,
                    },
                )
                await session.commit()
                return True
        except Exception as exc:  # normalized into durable status; never persist/log token material
            await self._mark_failure(job.id, exc)
            return False

    async def process_one(self) -> bool:
        async with self.session_factory() as session:
            job = await self.claim_next_job(session)
        if job is None:
            return False
        await self.process_job(job)
        return True
