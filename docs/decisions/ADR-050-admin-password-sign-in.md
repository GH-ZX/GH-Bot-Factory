# ADR-050: Admin password sign-in and self-service credentials

The Admin entry page offers username/password or an existing single-use, five-minute Telegram admin code. These authenticate tenant staff only; they never grant installation-level platform authority. BotFather tokens and the platform operator credential are not login codes.

Password sign-in resolves active user identities and verifies bounded PBKDF2 hashes off the async event loop, then resolves active staff memberships in active tenants. Username lookup is a pre-authentication identity boundary. A store slug is only a selector among verified memberships. Ambiguous identities or memberships fail closed; the server never chooses the highest role or first store silently. Password and password-update routes use the existing authentication rate limiter. Credentials and hashes are never returned or logged.

Authenticated staff may view their own username/store and set a first password through `/auth/account/password`. Changing an existing password requires its current value. The user row is locked and the session token version rechecked before writing the salted hash. The write increments token_version, revokes existing sessions and emits a tenant-scoped audit event without secret values. Username-less accounts require installation-owner assistance; no default credentials are created. Forgotten passwords are not automatically reset by a Telegram code while a password already exists.

Passwords use the existing User.hashed_password column, so no new migration is required. The previous operations migration remains part of the deployment prerequisite. Authentication response caching is disabled. Browser fields support password managers/paste, clear secrets after submission, expose accessible method tabs and a password-visibility control. Existing session persistence and backend membership revalidation remain in place.

The login source is tested for invalid credentials, store selection, password setup/change and revocation, plus browser token/password/mobile flows. This milestone is not evidence of an actual customer installation or production release qualification.
