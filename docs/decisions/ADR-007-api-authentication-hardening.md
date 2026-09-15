# ADR-007: API Authentication & Authorization Hardening

- **Status:** Accepted
- **Date:** 2026-09-15
- **Author:** GH-Bot-Factory Architecture Team

---

## 1. Context and Problem Statement

During Phase 5.0 ([`ADR-006`](file:///home/it/Coding/gh-bot-factory/docs/decisions/ADR-006-payment-abstraction.md)), GH-Bot-Factory established a multi-client REST API foundation under [`apps/api/v1/`](file:///home/it/Coding/gh-bot-factory/apps/api/v1/) serving Telegram Bots, Telegram Mini Apps, Admin Dashboards, and partner clients. To bootstrap the API layer, endpoints initially accepted caller identities via HTTP request headers (`X-Tenant-ID` and `X-User-ID`).

While functional for initial scaffolding, reliance on raw identity headers exposes critical security vulnerabilities:
1. **Identity Spoofing:** Any unauthenticated caller can forge `X-Tenant-ID` or `X-User-ID` headers to impersonate any user, read another customer's payment intents, or cancel/reconcile orders across tenant boundaries.
2. **Lack of Cryptographic Provenance:** There is no server-side guarantee that the caller authenticated via Telegram Mini App or any legitimate credential provider before executing payment operations.
3. **Absence of Session Invalidation:** Once an identity is assumed, there was no mechanism to revoke compromised sessions, force password/credential resets, or invalidate active client tokens.
4. **Boundary Confusion:** Payment provider webhooks (Stripe, Telegram Stars) and customer-facing API calls were conflated in terms of trust boundaries, risking security regressions where webhook bypasses could affect customer endpoints or vice versa.
5. **Missing Role-Based Access Control (RBAC):** Customer accounts were not authoritatively separated from merchant staff/admin accounts at the API layer, risking privilege escalation.

GH-Bot-Factory requires a hardened, cryptographically verifiable authentication and authorization architecture with zero trust in client-supplied identity parameters.

---

## 2. Decision & Architecture

We decided to completely eliminate raw identity headers for user authentication and replace them with short-lived, server-signed Bearer JSON Web Tokens (JWT) bound to an authoritative, immutable [`AuthenticatedPrincipal`](file:///home/it/Coding/gh-bot-factory/packages/core/auth.py#L64).

### 2.1 The 5 Security Invariants
All API endpoints and authentication services are governed by five inviolable security invariants:
1. **Identity Law:** *«Client-supplied user/tenant IDs are never authoritative.»* (No caller can assert their own identity via query params, body fields, or custom headers).
2. **Authorization Law:** *«Every customer API operation is authorized against the authenticated principal.»* (Resource access is strictly scoped to `principal.tenant_id` and `principal.user_id`).
3. **Mini App Law:** *«Telegram "initData" is authenticated cryptographically before deriving identity.»* (HMAC-SHA256 verification against the owning bot's token occurs prior to any user lookup or token issuance).
4. **Webhook Law:** *«Payment gateway webhooks are authenticated with provider-specific cryptographic verification and tenant/provider binding.»* (Webhooks operate on a distinct trust boundary and never accept customer JWTs).
5. **Tenant Law:** *«Authenticated tenant context cannot be overridden by request headers/body/query parameters.»* (Operations execute exclusively within `principal.tenant_id`).

---

### 2.2 Core Authentication Service ([`packages/core/auth.py`](file:///home/it/Coding/gh-bot-factory/packages/core/auth.py))

We introduced [`AuthTokenService`](file:///home/it/Coding/gh-bot-factory/packages/core/auth.py#L91) responsible for issuing and cryptographically verifying short-lived API access tokens using HMAC-SHA256 (`HS256`):
- **Claims Schema:**
  - `sub`: Authoritative `user_id` (UUID).
  - `tenant_id`: Authoritative `tenant_id` (UUID).
  - `source`: Authentication origin ([`AuthSource`](file:///home/it/Coding/gh-bot-factory/packages/core/auth.py#L14) enum: `TELEGRAM_MINIAPP`, `API_KEY`, `SESSION`, `TEST`).
  - `roles`: Assigned roles (`CUSTOMER`, `STAFF`, `MANAGER`, `ADMIN`, `OWNER`).
  - `token_version`: Integer tracking the user's active session epoch for instant revocation.
  - `iat` / `exp`: Issue timestamp and expiration (default TTL: 3600 seconds / 1 hour).
  - `iss` / `aud`: Standardized issuer (`gh-bot-factory`) and audience (`gh-bot-factory-api`).
- **Signature Verification:** Enforces strict signature validation, clock drift prevention, UUID schema parsing, and claim presence (`sub`, `tenant_id`, `source`, `exp`, `iat`).
- **Domain Exceptions:** Distinct hierarchy: [`TokenError`](file:///home/it/Coding/gh-bot-factory/packages/core/auth.py#L27), [`TokenExpiredError`](file:///home/it/Coding/gh-bot-factory/packages/core/auth.py#L33), [`TokenInvalidSignatureError`](file:///home/it/Coding/gh-bot-factory/packages/core/auth.py#L39), [`TokenMalformedError`](file:///home/it/Coding/gh-bot-factory/packages/core/auth.py#L45), [`TokenRevokedError`](file:///home/it/Coding/gh-bot-factory/packages/core/auth.py#L51), [`ForbiddenAccessError`](file:///home/it/Coding/gh-bot-factory/packages/core/auth.py#L57).

---

### 2.3 Authoritative Principal & FastApi Dependency Pipeline ([`apps/api/deps.py`](file:///home/it/Coding/gh-bot-factory/apps/api/deps.py))

Every protected endpoint resolves the caller via [`get_current_principal`](file:///home/it/Coding/gh-bot-factory/apps/api/deps.py#L23):
```
Client Request (Authorization: Bearer <token>)
                      │
                      ▼
 1. Extract Bearer Token (HTTP 401 if missing or invalid scheme)
                      │
                      ▼
 2. Verify Cryptographic Signature & Expiry via AuthTokenService
                      │
                      ▼
 3. Database Verification: User Lookup & is_active Check
                      │
                      ▼
 4. Session Revocation Check (token.token_version == user.token_version)
                      │ (Mismatch ➔ 401 Unauthorized: Access token has been revoked)
                      ▼
 5. Database Verification: Tenant Lookup & is_active Check
                      │
                      ▼
 6. Authoritative Membership Lookup (tenant_id + user_id)
                      │ (Not found ➔ 403 Forbidden: User not a member of tenant)
                      ▼
 7. Construct Immutable AuthenticatedPrincipal (User, Tenant, Role, Permissions)
```

The resulting [`AuthenticatedPrincipal`](file:///home/it/Coding/gh-bot-factory/packages/core/auth.py#L64) dataclass provides helper methods:
- `is_customer()`: Checks if caller holds `Role.CUSTOMER`.
- `is_staff_or_above()`: Checks if caller is `STAFF`, `MANAGER`, `ADMIN`, or `OWNER`.
- `is_admin_or_owner()`: Checks if caller is `ADMIN` or `OWNER`.

Role enforcement dependencies [`require_roles()`](file:///home/it/Coding/gh-bot-factory/apps/api/deps.py#L130) and [`require_staff_or_above()`](file:///home/it/Coding/gh-bot-factory/apps/api/deps.py#L146) provide declarative endpoint protection.

---

### 2.4 Instant Session Revocation (`token_version`)

To enable instant revocation of access tokens without maintaining an in-memory or Redis blocklist for millions of tokens:
1. A new column `token_version: int` (default 1) was added to the `users` table via Alembic migration [`b7e3a912f45c_add_user_token_version.py`](file:///home/it/Coding/gh-bot-factory/migrations/versions/b7e3a912f45c_add_user_token_version.py).
2. The user's active `token_version` is embedded in the signed JWT claims upon issuance.
3. Every API call validates that `payload["token_version"] == user.token_version`.
4. When a user logs out, resets credentials, or an administrator revokes sessions, `user.token_version` is incremented. All previously issued tokens immediately fail verification with `401 Unauthorized`.

---

### 2.5 Telegram Mini App Token Issuance ([`apps/api/v1/auth.py`](file:///home/it/Coding/gh-bot-factory/apps/api/v1/auth.py))

The Telegram Mini App authentication flow bridges Telegram's client-side cryptographic signature to the GH-Bot-Factory Bearer token lifecycle:
1. Frontend transmits `initData` and `bot_id` to `POST /api/v1/auth/telegram-miniapp`.
2. [`TelegramMiniAppAuthService`](file:///home/it/Coding/gh-bot-factory/packages/telegram/miniapp.py#L29) cryptographically validates `initData` against `bot.token_secret_ref`.
3. The platform resolves or provisions the authoritative [`User`](file:///home/it/Coding/gh-bot-factory/packages/tenants/models.py#L73) and [`Tenant`](file:///home/it/Coding/gh-bot-factory/packages/tenants/models.py#L21).
4. [`AuthTokenService`](file:///home/it/Coding/gh-bot-factory/packages/core/auth.py#L91) issues a short-lived signed Bearer JWT token.
5. Response returns `access_token`, `token_type: "bearer"`, and `expires_in: 3600`.
6. The Mini App includes this token in subsequent API requests: `Authorization: Bearer <access_token>`.

---

### 2.6 Customer Ownership Isolation in Payment Endpoints ([`apps/api/v1/payments.py`](file:///home/it/Coding/gh-bot-factory/apps/api/v1/payments.py))

Endpoints under `/api/v1/payments` were hardened against cross-customer tampering:
- **`POST /api/v1/payments/intents`:** Derives `tenant_id = principal.tenant_id` and `user_id = principal.user_id` authoritatively.
- **`GET /api/v1/payments/intents/{intent_id}`:** If `principal.is_customer()`, enforces `intent.user_id == principal.user_id`. Attempting to read another customer's payment intent raises `403 Forbidden`.
- **`POST /api/v1/payments/{intent_id}/cancel`:** Customers can only cancel their own intents; staff/admin can cancel any tenant intent.
- **`POST /api/v1/payments/{intent_id}/reconcile`:** Customers can only trigger reconciliation for their own intents.

---

### 2.7 Distinct Webhook Authentication Boundary

Payment gateway webhooks represent server-to-server callbacks from external financial institutions, not customer browser sessions:
- **Two Separate Trust Boundaries:**
  - **Boundary A (Customer API):** Authenticated via short-lived Bearer JWT access tokens; authorized by `AuthenticatedPrincipal`.
  - **Boundary B (Provider Webhooks):** Ingested via `/api/v1/payments/webhooks/{tenant_id}/{provider_name}` or candidate resolution. Authenticated via gateway-specific cryptographic signature verification against tenant's configured `webhook_secret_ref`.
- **Candidate Tenant Resolution:** Webhooks receive candidate tenant identifiers (from URL path or query). The tenant identifier is strictly unauthoritative until [`PaymentService.process_webhook()`](file:///home/it/Coding/gh-bot-factory/packages/payments/payment_service.py#L324) validates the cryptographic signature using that specific tenant's secret. Unsigned or mismatched payloads are rejected with `401 Unauthorized`.

---

## 3. Consequences

### Positive
- **Elimination of Header Spoofing:** Clients cannot forge identity headers (`X-Tenant-ID`, `X-User-ID`) to access data or manipulate orders.
- **Cryptographically Bound Identity:** Every authenticated request is backed by a server-signed JWT with proven provenance.
- **Instant Revocability:** Compromised tokens or expired sessions can be invalidated in $O(1)$ database operations via `token_version`.
- **Granular RBAC & Customer Privacy:** Customers cannot view, cancel, or tamper with orders or payments belonging to other users.
- **Architectural Clarity:** Webhook security is strictly separated from user session authentication, preventing accidental privilege bypasses.

### Negative / Trade-offs
- **Token Lifecycle Management:** Frontend clients (Telegram Mini App, web dashboards) must store Bearer tokens and handle token refresh or re-authentication upon expiry.
- **Database Overhead per Request:** Validating user/tenant activity and `token_version` requires database lookups during request dispatch (acceptable for current scale; Redis caching will be added in Phase 6).

---

## Security Hardening Addendum — Phase 5.1.1: JWT Signing Key Configuration

**Date:** 2026-09-15

Phase 5.1 originally allowed `AuthTokenService` to fall back to a repository-known development signing key when `JWT_SECRET_KEY` was absent outside production. That behavior is prohibited because any known fallback signing key can be used to forge otherwise valid Bearer tokens.

The authentication boundary now enforces the following signing-key rules:

1. `JWT_SECRET_KEY` is the only environment/configuration key accepted for JWT signing. Generic `SECRET_KEY` is not a JWT fallback.
2. No repository-known default JWT signing key exists.
3. `AuthTokenService()` fails closed when `JWT_SECRET_KEY` is missing or blank.
4. JWT signing keys must be at least 32 bytes to meet the minimum key size expected for HS256 use.
5. Explicit `secret_key=` injection remains available for isolated tests and controlled dependency injection, but production dependencies construct the service from `JWT_SECRET_KEY`.
6. Rotating `JWT_SECRET_KEY` invalidates tokens signed by the previous key because signature verification fails under the new key.
7. User-facing malformed-token errors do not include raw PyJWT exception text, and signing keys are never included in token errors or logs.
8. `.env.example` intentionally leaves `JWT_SECRET_KEY` blank and documents generation using a cryptographically secure random source. Copying the example without configuring a real key therefore fails closed instead of creating a predictable deployment secret.

These rules extend the Identity Law and Zero Secret Leakage law established by this ADR and the project constitution.
