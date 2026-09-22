# Web Factory Completion — 2026-09-22

This checklist supersedes the immediate next steps in the older delivery plan.
A Telegram owner/control bot is optional. Customer bots still need verified tokens when provisioned.

- [x] 1. Web-only setup and verified owner username/password; explicit installation authority separate from tenant roles.
- [x] 2. Shared visual tokens across builder, Admin/sign-in and setup.
- [x] 3. GH Store logo reused from sibling gh-store-dev.
- [x] 4. Responsive setup redesign with errors, retry, initialized and success states.
- [x] 5. Verify customer brief → factory review → one-time quote → tenant configuration.

Completion requires recorded verification; deployment and live login are tracked separately.
Real-customer Supabase/VPS testing, shopper polish, automated delivery packaging/reports and production qualification remain deferred.

## Verified completion

Source 240a106 is pushed and deployed. Migration a71c9e23b840; all services healthy.
Owner ahmedghx has the supplied password; no owner bot exists or is required.
Public browser login, factory checklist, Sales & leads with no extra token, logout,
mobile layout and already-configured setup all passed.

Final isolated-source make verify: 486 fast + 20 PostgreSQL, migration/no drift,
Ruff, secret/handoff/JS/compile/dependency checks. All four browser suites passed.
Changing --gh-brand in apps/shared/static/theme.css changes primary buttons and
logo tiles on build/Admin/setup; responsive screenshots were inspected.

Step 5 uses real API handlers/database state for brief → accepted one-time quote →
customer configuration, with Redis grants mocked. It preserves accepted scope and
denies customer access to factory sales. No real-customer bot launch, Supabase/VPS
installation, automated package/report or production release is claimed.

Live Admin: https://factory.gh-store.me/admin/
Sales navigation: Settings & operations → Sales & leads.
Full image, migration and backup evidence: ../operations/CURRENT_STATE.md.
