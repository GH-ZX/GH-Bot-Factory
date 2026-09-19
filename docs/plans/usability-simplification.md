# Usability simplification — 2026-09-20

Implement the ten accepted UX recommendations in the public configurator and tenant Admin. Preserve existing template keys, backend validation, pricing authority, and existing bots. No deployment is part of this checklist. Run verification after implementation, once; if it finds failures, repair and rerun only the affected checks.

- [x] 1. Present five understandable business categories; retain specialist presets under an optional chooser.
- [x] 2. Separate business category, product source, and appearance; preserve existing template configurations.
- [x] 3. Add a short goal-based recommendation with an explanation and an editable selection.
- [x] 4. Collapse advanced supplier, pricing, branding, integration, and hosting options with clear summaries.
- [x] 5. Add interactive, explicitly simulated storefront/chat previews for browsing, purchase, and delivery.
- [x] 6. Make onboarding actionable from Home, including product/payment setup and preview/launch checks without claiming simulated sales are real.
- [x] 7. Replace technical setup language with clear actions and contextual instructions.
- [x] 8. Group navigation around daily work, store setup, and advanced operations; retain role visibility.
- [x] 9. Unify hierarchy, spacing, focus states, responsive layouts, and primary actions.
- [x] 10. Save resumable non-secret setup drafts, preserve failed form inputs, and show contextual errors and BotFather help.
- [x] 11. Update handoff documents and add meaningful browser regression coverage.
- [x] 12. Run the final canonical verification and browser checks, inspect desktop/mobile screens, record results, and commit the completed milestone.

## Verification

Canonical `make verify` passed: 466 fast tests, 15 PostgreSQL tests, Ruff, migration upgrade/Alembic no drift, handoff/secret/JavaScript/compile/dependency checks clean. Existing Admin/Mini App browser regression and new setup UX browser regression passed. Desktop/mobile screenshots inspected; browser coverage includes drafts without tokens, tenant-separated draft restoration, failed submissions, simulated previews, collapsed choices, and mobile portrait/landscape layout. Final verification initially found trailing whitespace and a test synchronization issue; both were corrected, and the mobile wizard footer was improved before the passing rerun. No deployment or real Telegram/payment purchase was performed.

Screenshots: `artifacts/setup-public-desktop.png`, `artifacts/setup-public-mobile.png`, `artifacts/setup-admin-desktop.png`, `artifacts/setup-admin-mobile.png`. Logs: `/tmp/ghbf-ux-verify.log`, `/tmp/ghbf-ux-browser.log`.

## Implementation notes

Shared chooser and simulated previews are served from Admin static assets and reused by the configurator. Public appearance choices are inquiry preferences; they do not silently brand a provisioned bot. Admin drafts use session storage, expire after 24 hours, and are keyed by tenant, user, and edited bot. Tokens are excluded. Existing keys and backend contracts are preserved. Navigation uses Team for staff membership; it does not mislabel staff as customers.
