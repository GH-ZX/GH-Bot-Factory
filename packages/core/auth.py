import enum
import os
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from packages.tenants.models import Role


class AuthSource(str, enum.Enum):
    TELEGRAM_MINIAPP = "TELEGRAM_MINIAPP"
    API_KEY = "API_KEY"
    SESSION = "SESSION"
    TEST = "TEST"


class AuthError(Exception):
    """Base exception for authentication and authorization errors."""


class TokenError(AuthError):
    """Base token error."""


class TokenExpiredError(TokenError):
    """Access token has expired."""


class TokenInvalidSignatureError(TokenError):
    """Access token signature verification failed."""


class TokenMalformedError(TokenError):
    """Access token is malformed, missing required claims, or corrupt."""


class TokenRevokedError(TokenError):
    """Access token has been revoked or invalidated by version mismatch."""


class ForbiddenAccessError(AuthError):
    """User lacks required permissions or role for this resource."""


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    source: AuthSource
    roles: frozenset[Role] = frozenset([Role.CUSTOMER])
    permissions: frozenset[str] = frozenset()
    token_version: int = 1

    def is_customer(self) -> bool:
        """Returns True if the principal has CUSTOMER role."""
        return Role.CUSTOMER in self.roles

    def is_staff_or_above(self) -> bool:
        """Returns True if the principal has STAFF, MANAGER, ADMIN, or OWNER role."""
        return bool(self.roles.intersection({Role.STAFF, Role.MANAGER, Role.ADMIN, Role.OWNER}))

    def is_admin_or_owner(self) -> bool:
        """Returns True if the principal has ADMIN or OWNER role."""
        return bool(self.roles.intersection({Role.ADMIN, Role.OWNER}))


DEFAULT_JWT_SECRET = "gh-bot-factory-insecure-dev-secret-key-must-be-rotated-for-production-min256bit"
DEFAULT_ALGORITHM = "HS256"
DEFAULT_ISSUER = "gh-bot-factory"
DEFAULT_AUDIENCE = "gh-bot-factory-api"


class AuthTokenService:
    """Manages secure signing and verification of short-lived API access tokens."""

    def __init__(
        self,
        secret_key: str | None = None,
        algorithm: str = DEFAULT_ALGORITHM,
        issuer: str = DEFAULT_ISSUER,
        audience: str = DEFAULT_AUDIENCE,
    ) -> None:
        env = (os.getenv("ENVIRONMENT") or os.getenv("ENV") or "").lower()
        resolved_key = secret_key or os.getenv("JWT_SECRET_KEY") or os.getenv("SECRET_KEY")
        if not resolved_key and env in ("production", "prod"):
            raise RuntimeError("JWT_SECRET_KEY must be explicitly configured in production environments.")
        self.secret_key = resolved_key or DEFAULT_JWT_SECRET
        self.algorithm = algorithm
        self.issuer = issuer
        self.audience = audience

    def issue_access_token(
        self,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        roles: Sequence[Role | str],
        source: AuthSource,
        token_version: int = 1,
        expires_in_seconds: int = 3600,
        extra_claims: dict[str, Any] | None = None,
    ) -> str:
        """Issues a short-lived signed JWT access token.

        Contains only necessary claims: sub, tenant_id, source, roles, token_version, iat, exp, iss, aud.
        """
        now = datetime.now(UTC)
        payload: dict[str, Any] = {
            "sub": str(user_id),
            "tenant_id": str(tenant_id),
            "source": source.value,
            "roles": [r.value if isinstance(r, Role) else str(r) for r in roles],
            "token_version": token_version,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=expires_in_seconds)).timestamp()),
            "iss": self.issuer,
            "aud": self.audience,
        }
        if extra_claims:
            reserved = {"sub", "tenant_id", "source", "roles", "token_version", "iat", "exp", "iss", "aud"}
            safe_claims = {k: v for k, v in extra_claims.items() if k not in reserved}
            payload.update(safe_claims)

        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def verify_access_token(self, token: str) -> dict[str, Any]:
        """Verifies JWT signature, expiration, issuer, audience, and required claims."""
        if not token or not isinstance(token, str):
            raise TokenMalformedError("Token must be a non-empty string.")

        try:
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=[self.algorithm],
                issuer=self.issuer,
                audience=self.audience,
                options={
                    "require": ["sub", "tenant_id", "source", "token_version", "exp", "iat"],
                    "verify_exp": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
            # Validate UUID formats in claims
            uuid.UUID(str(payload["sub"]))
            uuid.UUID(str(payload["tenant_id"]))
            return payload
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpiredError("Access token has expired.") from exc
        except jwt.InvalidSignatureError as exc:
            raise TokenInvalidSignatureError("Access token signature is invalid.") from exc
        except (jwt.InvalidTokenError, ValueError, KeyError) as exc:
            raise TokenMalformedError(f"Malformed or invalid token: {exc}") from exc
