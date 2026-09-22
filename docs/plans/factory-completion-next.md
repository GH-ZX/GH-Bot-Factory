# Delivery, settings, releases and MiniApp — 2026-09-22

Source checkboxes are separate from deployment and testing. Owner explicitly requested no tests,
real-tenant trials, commits or pushes until asked.

- [x] Redeploy current customer-product work to the existing factory installation.
- [x] 3. Delivery center: readiness/missing requirements, package actions and handoff history.
- [x] 4. Tenant settings: organized branding, language, support, commerce and operational shortcuts.
- [x] 5. Support-to-release: linked fixes/releases/affected versions and update records.
- [x] MiniApp: complete customer navigation, product detail, accessible sheets, history and support.
- [x] Redeploy this completed coding batch.
- [ ] Owner-authorized tests and release qualification.
- [ ] Owner-authorized commit/push.
- [ ] Real-tenant trial (explicitly postponed).

Initial deployment: `gh-bot-factory:work-20260922-product`, uncommitted source; schema `b82da430c951`. Database backup: `backups/product-20260922/before-product.dump`. Docker reported all project services healthy; no acceptance tests. Final coding batch deployed as `gh-bot-factory:work-20260922-completion`; migration `c93eb541da62` applied successfully after `backups/product-20260922/before-completion.dump`. API, worker, bot runtime, PostgreSQL and Redis report healthy. Only the three application containers were recreated.


Implemented surfaces:

- Admin → Settings & operations → Delivery center: per-handoff plan, missing requirements,
  package actions, accepted scope, history, and explicit final receipt evidence.
- Admin → Store settings: public identity/notice/FAQ/support/policies/recipient guidance and
  direct navigation to bot branding, catalog/warranty, pricing/resellers/coupons and operations.
- Admin → Settings & operations → Releases & fixes: immutable release notes, classified issue
  links, affected versions and proposals using the recorded image. Support cases can be linked
  to sanitized bot issues without copying shopper messages.
- MiniApp: product detail dialog, Help view, FAQs/policies, recharge shortcuts, spending/asset/
  funding/deposit histories and existing-payment review, keyboard/inert sheet handling.
- Public design preview: `/miniapp/?preview=1` (sample data, business mutations disabled).

Code is not feature-verified. Real customer installation, payment behavior, migration drift,
concurrency and all visual/keyboard/RTL acceptance await the owner's testing instruction.
