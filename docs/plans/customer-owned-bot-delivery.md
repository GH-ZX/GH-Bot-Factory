# Customer-Owned Bot Delivery Completion Plan

Date: 2026-09-21. Status: public customer-builder redesign implemented and deployed; the complete delivery workflow remains pending. See `../operations/RESUME_CUSTOMER_DELIVERY.md` for the latest priority order and acceptance criteria.

## Product contract

GH-Bot-Factory sells configured Telegram commerce bots and Mini Apps delivered to the customer's own VPS/server and Supabase PostgreSQL project. The factory owner receives inquiries, prepares/approves scope and one-time pricing, generates delivery artifacts, installs them, and hands over the customer admin and localized documentation. Mandatory factory subscriptions are outside this requested product flow. Customer infrastructure and supplier/payment charges remain separate expenses. Later integrations are separately quoted additions.

Public discovery -> guided requirements -> factory inquiry -> reviewed quote -> accepted scope -> configuration -> validated delivery package -> owner installs on customer infrastructure -> acceptance -> localized handoff.

Existing integrations can be configured automatically. An unfamiliar API requires documentation review, adapter/mapping implementation where needed, credential checks, and purchase/reconciliation testing before it is offered as supported. Do not advertise arbitrary API integration as automatic.

## Inspected baseline and gaps

- `apps/configurator/static/`, public marketplace routes, sales console, quote/onboarding services, and integration catalog provide a reusable acquisition foundation.
- `packages/marketplace/quotes.py` currently adds recurring fees for dedicated/Supabase delivery. This conflicts with the requested one-time delivery model.
- `packages/marketplace/handoff_service.py` generates an encrypted tenant snapshot and manifest, not a complete installable customer release. Export success does not prove destination installation or runtime cutover.
- `docker-compose.yml` uses local PostgreSQL dependencies. Customer Supabase deployment needs its own supported Compose configuration, health checks, and migration connection contract.
- `packages/core/database.py` includes Supabase helpers, including transaction-pooler URL construction. These helpers are not proof of end-to-end Supabase migration/runtime compatibility.
- Existing catalog, ledger, payments, fulfillment, pricing/reseller, admin, audit, and storefront foundations should be validated through actual customer journeys.
- Search of packages/apps/migrations found warranty references in descriptive copy but no identified implementation for warranty claims, promotional coupons, support tickets, or broadcasts. Treat these as unverified/missing until a deeper feature audit proves otherwise.
- The working tree already contains unrelated auth/UI/report changes. Preserve them. Historical verification reports are not fresh evidence for this plan.

## Ordered work and acceptance criteria

Priority update: the owner prioritized design. The public builder portion of step 5 is delivered; tenant Admin/shopper polish, language behavior and the full installation flow remain open. Tomorrow begins with reviewing that page and closing the inquiry-to-one-time-quote flow.

- [ ] 1. Align commercial flow: one-time base package plus selected integrations/custom work; no required subscription; separate existing integration from request-new-API. Retain immutable accepted quotes and authorized platform operations. Done when dedicated customer quotes have no mandatory recurring factory charge and a new integration request reaches owner review.
- [ ] 2. Prove one customer delivery first: generate a reproducible release for a fresh customer Supabase project and clean VPS/test host. Done when migration, initial owner setup, bot startup, Mini App, payment and one purchase work without factory runtime dependency. Record external evidence; do not infer success from local PostgreSQL alone.
- [ ] 3. Automate packaging: durable, retry-safe package jobs and visible preparing/validating/ready/failed status in factory Admin; manifest binds customer scope, image digest, schema head, checksums and validation results. Done when retrying generation cannot mix customers or silently change accepted scope.
- [ ] 4. Finish tenant operations: catalog ordering/naming/images, fixed and margin pricing, warranties, coupons, reseller tiers, broadcasts, support, member controls, alerts and complete operational history. Audit existing screens/services first. Done when each advertised function has a working end-to-end flow and permission checks.
- [ ] 5. Polish discovery, tenant Admin and shopper journeys: mobile-first layout, Arabic RTL/English LTR, translated configurable messages, clear errors and pending states, reduced-motion support, lightweight animation. Bot menu: Shop / Recharge / Profile / Orders / Support / Language. Done when real Telegram clients and mobile browsers pass task-based acceptance.
- [ ] 6. Qualify customer release: make verify, make release-gate, immutable-image checks, PostgreSQL concurrency, Supabase installation, restore rehearsal, upgrade rehearsal, and real Telegram/provider acceptance appropriate to advertised integrations. Done only with recorded evidence and updated handoff docs.

Architectural/security/accounting changes in these steps require ADRs when implemented; this plan does not silently replace current invariants or alter persisted commercial records.

## Customer delivery package contract (to implement)

1. Database: one release-specific `install.sql` for a fresh destination, generated from the authoritative migration chain and verified against the same image/schema head; migration metadata and subsequent versioned upgrade path remain intact. Include database setup, TLS, permissions and backup/restore instructions. Do not apply bootstrap SQL blindly to an existing database. Existing-customer moves use the supported encrypted tenant import contract separately.
2. Runtime: immutable image digest (plus optional image archive for offline loading), customer `compose.yaml`, `.env.example` containing placeholders, and install/start/health/backup/upgrade scripts. API, worker, bot runtime and migration job share an image; Redis remains a separate rebuildable service. A single image does not imply a single process/container.
3. Configuration: customer branding/languages/modules/integration scope and destination-owned connection settings. Provision tokens through SecretStorage. Reports, ordinary archives and Git must not contain plaintext secrets; destination .env is never copied from the factory. Securely deliver any encrypted customer secret material separately from its unlock credentials.
4. Localized report: purchased scope, installed version, requirements, credential checklist (no secret values), setup and admin guide, integration limits, warranties/support policy, backup/restore/upgrade steps, acceptance evidence and support contact. Arabic output requires RTL and visual review; English output uses LTR. This is per-customer output, not a generic project overview.
5. Independence: no factory-only admin access, global customer data, or required factory billing connection. Tenant admin can operate its own installation. Verify source bot has actually stopped before starting the same Telegram identity at the destination; provide a safe recovery path.

## Tenant and shopper acceptance matrix

| Area | Required customer-visible result |
| --- | --- |
| Catalog | Rename/reorder categories and products, edit images/descriptions, availability, stock and variants |
| Pricing | Fixed price, margin, reseller tier and coupon rules with deterministic precedence and immutable purchase quotes |
| Warranty | Purchase-time terms, claim eligibility/expiry, evidence, replacement/refund decision, audit history and duplicate protection |
| Orders | Search/history, delivery details, meaningful pending/error state, safe reviewed resolution; ambiguous upstream acceptance must reconcile before retry/refund |
| Branding | Logo/colors/messages, Arabic/English, previews, supported Telegram media/custom emoji with ordinary fallback |
| Users | Profile/ID/wallet/history, roles, restrictions and ledger-backed adjustments |
| Support | Shopper ticket, conversation, admin reply, status, notifications and order-linked context |
| Announcements | Audience selection, preview, queued delivery, progress and retry/rate-limit handling |
| Alerts | Failed/stale orders, payment review, supplier balance, connection failures and expiring operational prerequisites |
| Shopper purchase | Browse/search, clear authoritative price, recharge, purchase once, receive delivery, review history and request support |
| Durability | Restart during payment/fulfillment without duplicate credit, purchase or refund; restore data and secret vault successfully |

## Supabase and Telegram implementation references

- Supabase recommends direct connections for migrations and long-lived sessions; session pooling is an IPv4 alternative. Use the actual project connection details and verify TLS, pool sizes and driver behavior: https://supabase.com/docs/guides/database/connecting-to-postgres
- Keep database access behind the authenticated backend. Verify Supabase Data API grants/exposed schemas cannot bypass the application's tenant/financial boundaries; never place database credentials or service-role keys in Mini App assets.
- Telegram custom emoji capabilities must be checked against current official requirements and the actual bot before promising them: https://core.telegram.org/bots/features

## Definition of complete

A factory inquiry can become an approved, reproducible delivery; the factory owner can install it on a fresh customer Supabase project and VPS using only its documented artifacts; the customer can administer and sell independently; shoppers can recharge, buy, receive fulfillment and obtain support; failure recovery, upgrades and restore are proven. Public marketing claims must match the tested release.
