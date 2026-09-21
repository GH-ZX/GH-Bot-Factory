# Admin identity, one-time quotes and tenant operations

User instruction (2026-09-21): implement steps 1 and 3; align Admin with the public cream/green design. Step 2 ends with a real-customer test later. Steps 4, 5 and 6 explicitly remain incomplete.

- [x] Step 1: cohesive Admin identity, readable customer brief, one-time customer-owned quote creation and acceptance.
- [x] Step 3: implemented tenant-side catalog controls, pricing/resellers, support tickets, warranty review, coupons, announcements, user/order/history and alerts; verify boundaries and browser flows.
- [ ] Step 2: real-customer Supabase/VPS installation acceptance — pending later.
- [ ] Step 4: complete shopper UI/Telegram journey and bilingual polish — deferred/incomplete.
- [ ] Step 5: automated delivery package and localized reports — deferred/incomplete.
- [ ] Step 6: production release qualification and complete handoff evidence — deferred/incomplete.

Implementation checklist (source implementation; live deployment/acceptance are separate):
- [x] Readable structured inquiry requirements; immutable quote scope; legacy quote preservation.
- [x] One-time pricing for customer-owned delivery; no mandatory recurring factory fee on new dedicated/Supabase quotes.
- [x] Shared visual identity throughout Admin, including login, navigation, forms, dialogs and mobile layouts.
- [x] Tenant operations schema/services/routes with tenant isolation, RBAC and audit.
- [x] Coupon checkout accounting and concurrency/idempotency checks.
- [x] Warranty terms captured at sale and manual claim decisions without implicit refunds.
- [x] Support messages/history and announcement queue/delivery status.
- [x] Admin workflows for all new capabilities and existing catalog/pricing/user controls.
- [x] Final isolated-source verification and handoff documentation.
- [x] Commit and push the verified milestone.

Existing unrelated workspace edits are preserved. No new live messages, payments, purchases, or customer installation is performed as part of automated tests.

Warranty approval is a manual decision record. Shopper support/coupon entry is deferred with step 4; package/report automation and production evidence remain incomplete. Current selectors list 100 members/orders; campaigns are bounded to 10,000 recipients.

Final source verification (2026-09-22): `make verify` passed, 474 fast + 19 PostgreSQL. Exact-source Admin operations and Admin/Mini App browser regressions passed. Earlier workspace run passed 478 fast + 18 PostgreSQL, including unrelated auth tests.

Implementation commit `1d4fcec` was pushed to `origin/main` on 2026-09-22. Canonical isolated-source gate: 474 fast + 19 PostgreSQL; browser workflows passed. Live deployment and actual customer acceptance remain pending.
