# Phase 14 — Customer Marketplace, Tenant Handoff, and Integration Catalog

> **Status:** Product and architecture proposal recorded on 2026-09-17. No application code, schema, route, or deployment change is authorized by this document.

## Product goal

GH-Bot-Factory should support the complete commercial relationship around a Telegram bot or Mini App:

1. A prospective customer discovers the available products and understands every template.
2. The customer configures what they want and sends a request to the Factory Owner.
3. The Factory Owner reviews the request, recommends providers, prices the work, and manually approves the project.
4. The Factory Owner provisions a tenant and makes the customer its `OWNER`.
5. The customer manages their own catalog, inventory, supplier credentials, product mappings, retail prices, payments, staff, and orders.
6. The customer's shoppers buy through that tenant's bot or Mini App.
7. The customer can remain on managed hosting or purchase a separately priced deployment/source license and move to an installation they control.

Phase 14 should productize the existing factory. It should not copy a new application for every customer or weaken the shared multi-tenant, pricing, payment, ledger, provider, and fulfillment boundaries.

## Actors and authority

| Actor | Responsibilities and authority |
|---|---|
| **Factory Owner / Platform Operator** | Owns the installation-wide offering catalog, templates, integration offerings, commercial prices, leads, quotes, tenant provisioning, plan assignment, managed hosting, and platform audit. This is installation-level authority, separate from tenant RBAC. |
| **Customer / Tenant Owner** | Owns one tenant's business operation: products, inventory, provider connections, provider credentials, mappings, retail pricing, payment methods, staff, orders, branding, and bots. The customer receives the tenant `OWNER` role. |
| **Customer Staff** | Receives tenant-scoped roles and performs only the operations allowed by tenant RBAC. |
| **Shopper / End Customer** | Buys from one tenant's bot or Mini App and may access only their own wallet, payments, and orders. |
| **Self-hosted Customer** | Operates a separate installation after a formal deployment or source-license handoff. The original Factory Owner no longer has technical control unless the customer separately grants support access. |

The Factory Owner should not perform routine catalog entry or provider credential management for the customer. Support should use audited diagnostics and explicit customer cooperation, never silent impersonation.

## What the current templates mean

The Admin Bot Factory currently exposes eleven versioned templates. Phase 14 should expose the following explanations through a reusable **? Help** drawer in the Admin wizard and on the public configurator. The same server-owned metadata should feed both views so their descriptions cannot drift.

| Template | Meaning | Best fit | Typical product source |
|---|---|---|---|
| **General Commerce** | A balanced store with catalog, order history, and account features, without a specialized supplier workflow. | A first store, mixed manually managed catalog, or a simple service business. | Primarily customer-managed products; compatible automation may be configured later. |
| **Digital Goods** | A delivery-focused store for keys, subscriptions, downloads, and other digital stock. | Businesses that care most about fast fulfillment and delivery history. | Stored digital inventory or a simple mapped supplier. |
| **Gift Cards** | A straightforward denomination-based gift-card and voucher catalog. | A small catalog of cards or vouchers that the customer manages. | Stored codes or manually managed inventory. |
| **Gaming Store** | A higher-energy storefront for game credits, memberships, top-ups, and codes. | Gaming communities and virtual-goods sellers. | Stored products, assisted fulfillment, or compatible provider products. |
| **Services** | A clean fixed-price service catalog with order/status tracking. | Setup work, consulting, digital services, and other assisted delivery. | Customer-defined services, normally fulfilled manually or through a compatible service provider. |
| **Multi-API Reseller** | A provider-agnostic reseller store with multiple supplier categories, routing, wallet funding, and margin controls. | A reseller aggregating several APIs and dynamically selecting suppliers. | Provider APIs, optionally mixed with stored inventory. |
| **Numbers & SMS** | A specialized number reservation and SMS activation store with status polling. | 5sim-style number/SMS activation services. | Number-provider APIs. |
| **Accounts Store** | A store for account inventory supplied through one or more account providers. | Sellers of delivered account credentials with priority/fallback routing. | Stored accounts and/or account-provider APIs. |
| **Gift Cards & Codes** | A multi-provider reseller version of the basic Gift Cards template, with supplier-aware prices and cost routing. | Larger gift, game-code, prepaid, and voucher resellers. | Gift and digital-product provider APIs, optionally with stored codes. |
| **Digital Products Reseller** | A general API-backed digital catalog for subscriptions, codes, services, and other digital products. | OpenAPI/Swagger-backed resellers and mixed digital catalogs. | Digital-product, gift, and service APIs. |
| **Hybrid Store** | One storefront spanning numbers, accounts, gifts, digital products, and services. | Customers who need several business types in one bot. | Multiple provider categories plus stored inventory. |

The help drawer should also show, for each template:

- what the customer can sell;
- whether it is designed for stored inventory, provider APIs, or both;
- required and optional integrations;
- delivery behavior and shopper experience;
- default routing strategy;
- operational complexity and likely setup work;
- limitations and an example business;
- which hosting and support packages are available.

Template selection remains a starting configuration. It must never grant a provider entitlement, make a supplier credential available, or bypass tenant validation.

## Recommended public customer journey

Create a modern public **Build Your Bot** page, linked from `gh-store.me`, with a guided configurator:

1. **Choose the product:** Telegram Bot, Telegram Mini App, or Bot + Mini App. A Mini App option should explain that Telegram launches it through a companion bot.
2. **Choose the business template:** searchable cards, examples, filters, and the shared template help content.
3. **Choose product sources:** stored inventory, provider APIs, or hybrid.
4. **Choose integrations:** compatible supplier/API and payment add-ons, with requirements and price labels.
5. **Choose delivery:** managed hosting, dedicated portable deployment, or premium source-code license.
6. **Choose support:** setup only, maintenance, managed operations, or custom work.
7. **Review the estimate:** a server-calculated itemized estimate and clear recurring/one-time labels.
8. **Send the request:** contact details, project notes, preferred contact method, and an inquiry/message thread with the Factory Owner.

The first release should be a request-and-quote workflow. The configurator should not automatically provision a tenant or charge the prospect. This preserves the desired manual review while the commercial process matures.

Recommended public UI behavior:

- searchable and filterable template/integration cards;
- a side-by-side template and hosting comparison;
- a step-by-step configurator with a persistent summary and live estimate;
- mobile-first, accessible forms with saved progress;
- clear labels for one-time setup, recurring hosting, integration access, upstream provider fees, and source licensing;
- a direct Telegram contact option plus a persistent website inquiry thread;
- honest availability states such as available, requires review, waitlist, deprecated, or custom quote.

Public inquiry forms must be rate-limited and must never accept provider tokens, bot tokens, payment secrets, or other operational credentials.

## Sales, quote, and onboarding workflow

```text
Prospect browses and configures
              |
              v
      Customer inquiry/request
              |
              v
 Factory Owner reviews and recommends
              |
              v
 Versioned, expiring commercial quote
              |
              v
       Customer accepts / pays
              |
              v
 Tenant + customer OWNER membership
              |
              v
 Customer configures catalog/providers
              |
              v
  Launch-readiness review and go-live
```

The quote should freeze its currency, line items, discounts, taxes where applicable, validity period, license terms, and accepted revision. Browser-calculated prices are display-only; the server must resolve all authoritative prices from the platform catalog.

The Factory Owner may adjust or replace a draft quote. An accepted quote becomes immutable evidence. Existing `SaaSPlan` and `TenantSubscription` records remain suitable for recurring feature entitlements, but they should not be overloaded with one-time setup work, custom development, or source-license terms.

After approval, onboarding should:

- create or select the tenant;
- grant the customer tenant `OWNER` authority;
- assign the approved plan and integration entitlements through the platform control plane;
- provision the bot and optional Mini App shell;
- guide the customer through branding, products, inventory, provider credentials, mappings, prices, payments, and staff;
- run server-authoritative launch readiness before go-live.

## Commercial catalog and balanced pricing

The price shown to a prospect should be a composition of separately controllable items:

| Price component | Examples |
|---|---|
| **Product** | Bot, Mini App companion experience, or combined package |
| **Setup/template** | Standard setup, advanced reseller configuration, custom branding |
| **Recurring plan** | Tenant limits, bot count, feature flags, support level |
| **Hosting/maintenance** | Managed hosting, backups, updates, monitoring |
| **Integration add-on** | Access to a provider adapter or payment integration |
| **Integration setup** | Credential onboarding, mapping, testing, catalog import |
| **Usage component** | Optional metered platform fee when the contract requires it |
| **Custom work** | New provider adapter, custom workflow, design, migration, training |
| **Deployment/source license** | Dedicated deployment package or licensed source delivery |

Each price needs a currency, billing type, amount, effective dates, active/retired state, and eligibility rules. Quotes should snapshot these values so future catalog edits do not rewrite a customer's accepted deal.

Three kinds of money must stay distinct:

1. what the customer pays the Factory Owner for the product, hosting, support, and integrations;
2. what the customer pays an upstream supplier/API;
3. what the customer's shoppers pay the customer.

The existing commerce pricing engine remains authoritative for shopper prices and tenant margin. Platform offering prices and supplier costs must never be treated as shopper checkout prices.

## Integration/API marketplace

Phase 14 should add a platform-owned integration catalog. All customers may browse the catalog, while actual use depends on plan or paid entitlement.

Each integration offering should describe:

- adapter key, provider name, canonical category, and supported capabilities;
- business templates it supports;
- authentication/credential requirements without revealing any secret value;
- upstream account and upstream fee responsibility;
- platform setup, recurring, and optional usage prices;
- support level, documentation, test status, and geographic/currency constraints;
- lifecycle state: `DRAFT`, `SANDBOX_REVIEW`, `ACTIVE`, `DEPRECATED`, or `RETIRED`.

The catalog is global platform data. The actual provider connection is tenant-owned. A customer supplies their own credentials through the authenticated tenant Admin, and those values cross only the write-only `SecretStorage` boundary. Purchasing an integration grants eligibility; it never shares another tenant's provider account or credentials.

Adding a new API should follow one of two paths:

1. a reviewed built-in adapter for deeper provider-specific behavior; or
2. the existing constrained declarative HTTP/OpenAPI mapping for compatible APIs.

Publication should require documentation, capability/category declaration, allowed hosts, credential schema, sandbox evidence, redaction checks, timeout/ambiguity behavior, fulfillment reconciliation, and operator approval. Arbitrary uploaded code or arbitrary HTTP destinations should remain prohibited.

## Managed hosting, portable deployment, and source delivery

Offer three clearly different products:

| Delivery model | Customer receives | Factory Owner control |
|---|---|---|
| **Managed subscription** | A tenant, configured bot/Mini App, updates, backups, and support on the Factory Owner's installation. No source delivery. | Full operational control of the managed installation, subject to the customer's tenant authority and contract. |
| **Dedicated portable deployment** | A separately deployed, single-customer installation package and documented operations handoff. Source may remain licensed/closed. | Only the access explicitly retained in the support agreement. |
| **Source-code license/buyout** | A sanitized, customer-specific repository or licensed distribution, deployment documentation, and agreed update rights. | No automatic control after handoff; access requires the customer's explicit grant. |

The shared multi-tenant factory repository should not be handed to a customer as if it were generated bot code. A source package must exclude every other tenant's data, secrets, audit records, platform credentials, commercial catalog, and internal operational material. Its license must state whether use is perpetual or term-based, single-installation or multi-installation, transferable or non-transferable, and whether updates/support are included.

A self-hosted handoff should include:

- a signed commercial/license record and exact delivered version;
- a dedicated tenant export or clean single-tenant installation path;
- destination-owned `.env`, database, vault, domains, backups, and operator credentials;
- Telegram bot token, provider credential, and payment-secret rotation where practical;
- disabling the old managed runtime before the new runtime starts, preventing duplicate Telegram polling or duplicate processing;
- acceptance tests and a signed handoff checkpoint;
- a local state such as `EXTERNALLY_HOSTED` or `HANDED_OFF`, used for support/history rather than remote control;
- a defined retention/deletion window for the former managed tenant data.

There should be no hidden remote kill switch. Continuing control or maintenance belongs in a support contract and explicit access arrangement.

## Proposed domain concepts

These are planning names, not approved schema names:

- `PlatformOffering`: Bot, Mini App, bundle, hosting, support, source license, or custom work.
- `OfferingPrice`: versioned one-time, recurring, or metered platform price.
- `IntegrationOffering`: global catalog entry tied to an adapter/capability manifest.
- `CustomerInquiry` or `ConfigurationRequest`: prospect identity, desired configuration, status, and contact preference.
- `CommercialQuote` and immutable `CommercialQuoteLine`: owner-approved commercial proposal and accepted snapshot.
- `InquiryThread` / `InquiryMessage`: prospect-to-owner communication with audit and abuse controls.
- `TenantIntegrationEntitlement`: a tenant's right to configure a cataloged integration.
- `DeploymentHandoff`: managed-to-external transfer state and acceptance evidence.
- `SourceLicense`: scope, version, support/update rights, and commercial reference without embedding legal documents or secrets in logs.

Platform-global commercial records must never contain tenant operational data. Once a prospect becomes a customer, all tenant-owned business records must carry `tenant_id`. Platform mutations require platform authority and platform audit; tenant roles cannot change global offerings, prices, entitlements, or handoff state.

## Delivery plan

### Phase 14.0 — Product contract and template guidance

- Confirm actor names, managed/self-hosted products, licensing choices, commercial line items, and inquiry states.
- Write ADRs for the public sales boundary, platform commercial catalog, and deployment/source handoff before adding schemas.
- Extend the server-owned template metadata with structured explanations.
- Add the **? Help** drawer to the Admin bot wizard and reuse its content publicly.

**Exit:** The Factory Owner can explain every template and delivery model without reading source code; no commercial promise is ambiguous.

### Phase 14.1 — Public catalog, configurator, and messaging

- Add a public, mobile-first Build Your Bot page with template/integration search and comparison.
- Add the guided configuration summary and server-authoritative estimate.
- Add rate-limited inquiries and a simple threaded message/contact flow.
- Keep approval and provisioning manual.

**Exit:** A prospect can understand the products, configure a request, see an itemized estimate, and contact the Factory Owner without submitting secrets.

### Phase 14.2 — Platform sales console and immutable quotes

- Add an owner-only platform UI for leads, requests, messages, offering prices, and quotes.
- Keep its authentication separate from tenant Admin and avoid exposing a long-lived platform token to browser storage.
- Freeze accepted quote revisions and audit every commercial mutation.

**Exit:** The Factory Owner can turn an inquiry into an accepted, auditable quote with no client-authoritative pricing.

### Phase 14.3 — Customer onboarding and tenant ownership

- Convert an approved customer into a tenant and tenant `OWNER`.
- Assign plans and integration entitlements through platform authority.
- Add a customer onboarding checklist for products, inventory, providers, mappings, prices, payments, staff, and launch readiness.
- Keep day-to-day business management in tenant Admin.

**Exit:** The customer can operate their own store without Factory Owner data entry, cross-tenant access, or platform authority.

### Phase 14.4 — Integration marketplace

- Add platform-managed integration offerings and lifecycle states.
- Add per-integration prices and tenant entitlements.
- Link offerings to existing adapter manifests and compatible templates.
- Add the reviewed publication workflow for built-in and constrained generic integrations.

**Exit:** All customers can browse available APIs, entitled tenants can configure them with their own credentials, and every platform fee is explicit.

### Phase 14.5 — Dedicated deployment and source-license handoff

- Implement handoff records, version/license evidence, export/install workflow, token rotation, managed-runtime shutdown, and acceptance checks.
- Define which assets are licensed, what support includes, and how updates are delivered.
- Prove that the export contains only the intended tenant and no platform/other-tenant secrets or data.

**Exit:** A customer can leave managed hosting safely, and both parties have an auditable boundary for ownership, access, support, and responsibility.

### Phase 14.6 — Release qualification and business operations

- Add metrics for inquiry conversion, quote status, onboarding delay, integration adoption, recurring revenue, and handoff status without mixing them with tenant shopper analytics.
- Run canonical PostgreSQL concurrency, authorization, secret, pricing, export-isolation, browser, Docker, staging, restore, and portability gates.
- Complete the existing trusted HTTPS and release-evidence work before any public launch.

**Exit:** The complete customer journey is safe to operate and has reproducible release evidence.

## Recommended first commercial release

Keep the initial offer small enough to operate manually:

1. Managed Bot
2. Managed Bot + Mini App
3. Optional provider/API add-ons
4. Optional setup and monthly support
5. Custom-quoted dedicated deployment or source license

Launch the template help, public configurator, inquiry messaging, owner quote console, and customer tenant onboarding first. Add automated billing for these commercial products only after the manual process shows which packages and prices customers actually choose.

## Decisions to make before implementation

The Factory Owner should choose:

1. the launch currencies and tax/invoice policy;
2. the first three managed plans and their bot, feature, support, and integration limits;
3. whether each integration is included, one-time setup, recurring add-on, usage-priced, or custom quote;
4. which existing templates appear publicly at launch;
5. the exact difference between dedicated deployment and source-code license;
6. source license ownership, reuse, resale, update, exclusivity, and support terms;
7. contact channels and expected response times;
8. data-retention and support-access rules after a self-hosted handoff.

These decisions should become ADRs and commercial policy before Phase 14 schemas or public claims are implemented.
