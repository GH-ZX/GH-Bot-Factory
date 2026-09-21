# Web Factory Completion — 2026-09-22

This checklist supersedes the immediate next steps in the older delivery plan.
A Telegram owner/control bot is optional. Customer bots still need verified tokens when provisioned.

- [ ] 1. Web-only setup and verified owner username/password; explicit installation authority separate from tenant roles.
- [x] 2. Shared visual tokens across builder, Admin/sign-in and setup.
- [x] 3. GH Store logo reused from sibling gh-store-dev.
- [x] 4. Responsive setup redesign with errors, retry, initialized and success states.
- [x] 5. Verify customer brief → factory review → one-time quote → tenant configuration.

Completion requires recorded verification; deployment and live login are tracked separately.
Real-customer Supabase/VPS testing, shopper polish, automated delivery packaging/reports and production qualification remain deferred.

## In progress

Implementing web initialization and explicit SystemInstallState.operator_user_id. Existing platform environment-token automation remains supported. Tenant OWNER alone never grants installation access. No live migration or credential change yet. Source/API tests pass; all four browser suites passed (setup states/shared variables/logo; Admin auth; operations/password-session sales; builder setup). Step 1 remains open until verified live login. Workspace canonical make verify passed: 486 fast + 20 PostgreSQL, migration upgrade/no drift, Ruff, secret/handoff/JS/compile/dependency checks. Final isolated-source make verify also passed: 486 fast + 20 PostgreSQL. Deployment and live owner login pending.

Step 5 evidence: tests/test_web_factory_setup.py exercises real API handlers and database state from public inquiry through owner-password sales review, accepted one-time quote, customer creation and initial template/scope. Redis grants are mocked, no live Telegram/Supabase/VPS is used. No actual bot launch or package delivery claimed.
