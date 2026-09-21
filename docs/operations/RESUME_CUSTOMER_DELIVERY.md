# Resume: Customer-Owned Telegram Store Delivery

Checkpoint: 2026-09-21. Read this with CURRENT_STATE.md and the project laws in AGENT_MAP.md.

## What the owner wants

A public page on a GH Store subdomain advertises Telegram commerce bots and Mini Apps. A prospective customer chooses their store, features, integrations, branding and language. Their request reaches the factory owner's Admin sales panel. The owner reviews scope and price, configures the store, and eventually generates a complete installation package automatically.

The business model is a one-time configured delivery with extra charges for additional integrations/custom work, not mandatory factory subscriptions. Customer hosting and supplier/payment expenses are separate. Customers can request additional APIs later; unfamiliar APIs need compatibility review and implementation/testing.

The factory owner installs the store on the customer's VPS/server and Supabase project. Each customer receives:

1. A fresh-install database migration file with setup/credential instructions and a supported future upgrade path.
2. Docker image/deployment files and scripts to connect to Supabase, start services, check health, back up, restore and upgrade.
3. A customer-specific report in their language explaining purchased features, setup, administration, support and handoff. Credentials themselves must not appear in the report.

The customer then operates independently. Their Admin needs product/category editing and ordering, images/messages, Arabic/English, margins/fixed prices, warranties, coupons, reseller pricing, announcements, user management, order/history views, alerts, and safe resolution of stuck purchases. Shoppers need catalog browsing, wallet recharge, profile/ID/balance/history, purchase/delivery and support tickets, with a fast, secure, polished bot and Mini App.

## Already in the project

| Area | Existing foundation | What it does not prove |
| --- | --- | --- |
| Acquisition | Public builder, inquiry capture, factory sales console, quote and onboarding services | Every new requested feature is implemented or automatically provisioned |
| Commerce | Catalog, server-authoritative checkout, wallet ledger, payment settlement/reconciliation and durable fulfillment | A real purchase has been validated on a fresh customer Supabase/VPS installation |
| Operations | Tenant Admin, roles, order/financial review, audit/analytics, provider management, margin/fixed/reseller pricing foundations | Every requested management task has a complete, polished UI |
| Delivery | Docker services, migration chain, encrypted tenant export and offline import | One complete generated Docker + database + localized-report package |
| Storefront | Telegram runtime and Mini App browsing, recharge, profile/order foundations | Complete support/warranty/coupon/broadcast flows or a finished bilingual design |

Warranty claims, promotional coupons, support tickets and announcements were not identified as complete implementations during the scoped inspection. Treat them as gaps requiring a focused audit, not as delivered because their names appear in the builder. Supabase helpers exist, but a complete customer installation still needs proof.

## What changed in this conversation

- Recorded the customer-owned delivery direction in `docs/plans/customer-owned-bot-delivery.md`.
- Rebuilt the public `/build/` page with a cream/green design, illustrated hero, responsive layout and four short steps: store, features, appearance/delivery, review/contact.
- Added 14 feature requests, supplier/payment choices, custom API requests, store/report languages, visual-style preferences and customer-owned hosting options.
- Added private tab drafts for non-contact choices, retry/error states, review/edit navigation and an interactive simulated preview.
- Choices reach the existing sales flow as configuration plus bounded project notes. These are requests, not entitlements or provisioning commands.
- Changed public presentation to a reviewed quote. The backend still calculates legacy recurring estimates; one-time pricing has NOT been implemented there.
- This was a public customer-builder redesign, not a full tenant Admin or shopper Mini App redesign. Language/style choices record preferences; they do not automatically localize or restyle a delivered bot.
- Code commit: `48a11be`. Deployment record: `81aab61`. Both pushed to main.
- Redeployed the complete factory Compose stack from clean commit `48a11be`. All five running services were healthy; migration job exited 0. Named data volumes preserved.

Live builder: http://10.70.5.5:8010/build/
Admin: http://10.70.5.5:8010/admin/
Subdomain `botfac.gh-store.me` could not be resolved from the deployment host. Public DNS/TLS readiness remains to be verified.

## Next steps, in order

### 1. Close the customer request → factory quote flow

Review the live builder with the owner first, since reducing overwhelm and improving design remain the immediate priority. Walk through one representative brief and confirm that every selection is clearly visible in the factory sales panel. Present features, languages, integrations and delivery requirements in readable groups. Keep unfinished/custom items visibly subject to review.

Then align the quote engine and sales UI with one-time customer-owned delivery: base setup plus integrations/custom work; no required recurring factory fee for this path. Keep third-party running costs distinct and preserve existing commercial records rather than deleting billing code blindly. Acceptance must bind an explicit scope and price.

**Done when:** one request can become a clear, approved one-time quote without losing any selected requirements or promising unimplemented features.

### 2. Prove one complete customer installation

Use one representative supported store and integration set. Produce the database bootstrap, customer Compose configuration, immutable image, destination configuration checklist and install/health scripts. Test on an empty customer Supabase project and clean server. Keep the versioned migration chain for upgrades; a single bootstrap file must not replace it.

**Done when:** the owner can install using only the package instructions; the tenant can log in; the bot and HTTPS Mini App run independently of the factory. Confirm source runtime shutdown before moving the same Telegram bot identity.

### 3. Complete the tenant's everyday operations

Audit each requested feature against an actual Admin screen and backend operation. Finish catalog/branding/pricing/user controls and history where incomplete. Implement or complete support tickets, warranty claims, coupons and announcements; validate reseller pricing and alerts. Define permissions and auditable decisions.

Stuck-purchase actions must reflect provider evidence. Do not allow a button to falsely mark completion or issue a second purchase/refund after an ambiguous upstream result.

**Done when:** the tenant can run the advertised store without database editing or developer intervention.

### 4. Finish the shopper journey and visual experience

Complete a real path: Start → browse/search → recharge → buy → receive delivery → inspect order/history → request support or warranty assistance. Make pending/failure states understandable. Polish both the bot menus and Mini App, including Arabic RTL/English LTR, editable messages/images, accessible motion and supported custom emoji with fallbacks.

**Done when:** real Telegram mobile clients pass these tasks, including interrupted/retried payments and purchases, without duplicate charges or fulfillment.

### 5. Automate the proven delivery package

Turn the manually proven installation into a durable factory job. Add prepare/validate/ready/failed status, safe retries, checksums/version manifest and a tenant-specific report generator. Separate secrets from ordinary artifacts. Make later integration additions an approved change with versioned configuration/migrations and upgrade instructions.

**Done when:** an accepted scope produces the three requested deliverables reliably, without mixing tenant data or including factory credentials.

### 6. Qualify the first complete handoff

Run `make verify` and the required release/staging gates. Exercise real Supabase installation, purchases, service restarts, ambiguous provider results, backup/restore and upgrades. Record what passed and what remains unsupported. Resolve the intended public hostname/TLS before public launch.

**Done when:** one fresh customer deployment can sell, recover, be restored and be upgraded using its delivered instructions. Historical phase labels and SQLite checks alone do not establish readiness.

## Tomorrow's starting task

Review the deployed customer page, then trace one project brief into the factory sales panel and implement the one-time quote flow. Keep the next milestone bounded to “customer request becomes an approved, readable scope and quote.” Continue customer-install packaging after that flow is coherent. Incorporate the owner's design feedback before expanding into other interfaces.

## Verification and workspace cautions

- Last source gate: 470 fast tests and 15 PostgreSQL tests passed on the working tree; Alembic reported no drift. Browser regression and desktop/mobile inspection passed.
- Deployment checks: live/readiness/Admin/build HTTP 200, exact public asset hashes, live four-step/browser smoke, container dependency check and all services healthy. No live inquiry/purchase was submitted during the deployment smoke test.
- Fresh image dependency resolution occurred during the build; a complete clean-image release gate was not run. No production-ready claim.
- Migration head: `e5f6a8b9c1d2`. Application image tags: `48a11be` and `local`; manifest `sha256:9d11a29119f2cd8c7e8715b04380768706bba3472032010e6c3e0d214d017153`. Previous API image retained as `before-builder-48a11be`.
- Existing uncommitted Admin/auth/setup edits and report/skill/generator files belong to earlier work. Preserve and review separately. They were present in workspace tests but excluded from the clean committed deployment. Do not assume those workspace changes are live.
- Keep tenant scoping, SecretStorage, authoritative prices, ledger-only wallet mutations, settlement/refund idempotency and ambiguous-result reconciliation intact. Never modify unrelated host services or tunnels. Port 8000 remains reserved.
