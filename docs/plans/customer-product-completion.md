# Customer Product Completion — 2026-09-22

Checkboxes below mean **source implementation**, not tested, released or deployed.
The owner explicitly requires no tests until requested. Previous milestone test counts
must not be used as evidence for these changes.

- [x] 1. Customer storefront implementation: five tenant-selectable themes with shared
  Admin/MiniApp palettes, English/Arabic labels and RTL, coupon input, order pagination,
  spending-wallet history, support conversations, warranty requests, cart shortcut,
  and Telegram recharge/support shortcuts. Existing checkout/settlement services remain authoritative.
- [x] 2. Delivery-package generator: encrypted tenant export plus fresh-database SQL,
  digest-based Docker Compose, destination configuration, install/update scripts,
  schema/file identity checks, manifest and English/Arabic handoff report.
- [x] 3. Optional diagnostic access: owner-issued, read-only, expires in 1–72 hours,
  revocable, rate limited and audited. Includes an operator CLI; no remote command execution.
- [x] 4. Issue/fix history: report, diagnosis, shared-core/custom scope, fixed release,
  and resolution events, available in Admin → Settings & operations → Bot care & updates.
- [x] 5. Version/update records: immutable current/new image references, owner approval,
  backup reference, installation/rollback evidence and documented controlled scripts.
  Statuses are owner-reported; this is not automatic deployment or evidence verification.

Release and acceptance remain unfinished:

- [ ] Run owner-authorized checks: Python/JavaScript, new API flows and PostgreSQL concurrency.
- [ ] Prove fresh SQL matches migrations and verify empty-destination encrypted restore.
- [ ] Check themes, mobile/RTL, keyboard access and shopper flows in a browser.
- [ ] Build/publish the matching immutable Docker image (or deliver a verified offline image archive).
- [ ] Generate and inspect an actual customer package from quiesced customer state.
- [ ] Apply migration `b82da430c951` and deploy the new source when authorized.
- [ ] Real-customer Supabase/VPS acceptance and production release gate, when requested.

## Boundaries and remaining polish

- Live deployment remains `240a106` / schema `a71c9e23b840`; new code is not live.
- No tests, verification gates, browser checks, migration execution or Docker operations were run.
- No push: pushing main triggers `.github/workflows/quality-gates.yml`, which would violate
  the owner's current no-tests instruction. Work remains in the working tree.
- Wallet history currently covers spending-wallet ledger entries; asset balances remain separate.
- Merchant/product/provider content and server error details are not machine-translated.
- Telegram premium custom emoji assets are not provisioned; native emoji/haptics and CSS motion
  are used, with reduced-motion support. This does not claim every possible Telegram skin is finished.
- Support diagnostics deliberately expose only limited installation metadata. Repairs are
  delivered as versioned images applied with customer approval, not interactive remote database edits.
- Existing bundles predate the new schema fingerprint and must be regenerated for this release.

See [ADR-052](../decisions/ADR-052-customer-owned-delivery-and-consented-maintenance.md).
