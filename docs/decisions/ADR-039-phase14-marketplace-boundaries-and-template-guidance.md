# ADR-039: Phase 14 Marketplace Boundaries and Template Guidance

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

Phase 14 productizes GH-Bot-Factory into a commercial platform where the Factory Owner can offer Telegram bots, Telegram Mini Apps, hosted subscriptions, API integrations, and self-hosted / source-code packages to customers.

Historically, all template details were known only through developer inspection of `packages/factory/templates.py`. There was no structured, human-readable guidance accessible inside the Admin panel or exposed to potential prospects. This caused ambiguity around:
1. Which template is right for stored inventory vs live supplier APIs vs hybrid stores.
2. The distinct authority boundaries between the **Factory Owner**, the **Customer / Tenant Owner**, the **Customer Staff**, the **Shopper / End Consumer**, and the **Self-Hosted / Source Customer**.
3. What happens when a customer requests their bot code or self-hosted deployment: the boundaries, licensing terms, and the loss of platform control.
4. How APIs and integrations are cataloged, priced, and granted to tenants without secret leakage or tenant cross-contamination.

To prevent drift between internal operational tooling and future public configurators, the template definitions and commercial guidance must be server-authoritative and shared.

## Decision

1. **Four-Tier Authority Boundary Model:**
   - **Factory Owner / Platform Operator:** Operates the platform infrastructure, plans, commercial pricing, integration catalog, leads/quotes, tenant provisioning, and platform audit log. Never mutates tenant catalog items or provider credentials directly.
   - **Customer / Tenant Owner (`OWNER` in RBAC):** Controls their tenant workspace. Adds their own stored products, uploads branding, enters their own supplier API credentials into write-only `SecretStorage`, sets retail prices, manages staff, and views tenant analytics.
   - **Shopper / End Consumer:** Shoppers interact only with the Telegram bot or Mini App of that specific tenant. They have access only to their own wallet, payments, and orders.
   - **Self-Hosted / Source Licensee:** A customer who purchases a dedicated deployment or source license receives an isolated export (or sanitized single-tenant distribution). The Factory Owner retains no automatic control or backdoor; ongoing maintenance requires an explicit support contract.

2. **Server-Authoritative Template Guidance:**
   Extend `BotTemplate` in `packages/factory/templates.py` with structured `TemplateGuidance`:
   - `product_source`: `stored`, `provider_api`, or `hybrid`.
   - `what_you_can_sell`: Concrete plain-language description of compatible goods/services.
   - `best_fit`: The ideal business profile or customer persona.
   - `delivery_experience`: What the shopper experiences during checkout and delivery.
   - `operational_complexity`: `Low`, `Medium`, or `High`.
   - `setup_requirements`: Key setup steps (e.g., inventory upload, supplier credentials, payment gateways).
   - `example_business`: Real-world example (e.g., "Fortnite & Steam game card reseller", "Virtual phone number SMS store").
   - `limitations`: Architectural constraints or out-of-scope capabilities.
   - `supported_hosting`: List of supported delivery models (`managed`, `dedicated`, `source_license`).

3. **Expose Guidance via Existing Admin API:**
   `GET /api/v1/admin/bots/templates` includes `guidance` within each template's `public_payload()`. Future public configurators (`/api/v1/public/templates` or similar) will consume the exact same payload.

4. **Admin Wizard `? Help & Guidance` Drawer:**
   In the Admin Bot Creation wizard (`apps/admin/static/index.html` and `app.js`):
   - Add a prominent `? Help & Guidance` trigger button next to the template selector.
   - Clicking opens a slide-over / modal drawer displaying all templates, their guidance details (sellable goods, API requirements, complexity badge, delivery style), and an immediate "Select This Template" action.
   - The drawer must be fully responsive, accessible on mobile (`max-width: 820px`), and styled according to the Admin dark theme.

5. **Financial Layer Separation:**
   Three financial layers remain strictly separated:
   - *Platform fee:* What the customer pays the Factory Owner (subscription, setup, API add-on entitlements).
   - *Supplier cost:* What the customer pays their upstream supplier directly.
   - *Retail price:* What the shopper pays the customer's bot, managed by tenant markup.

## Consequences

- The Factory Owner and tenant admins have instant, structured guidance inside the Admin interface without reading source code.
- Public "Build Your Bot" configurators in Phase 14.1 can consume the exact same guidance API with zero drift.
- No database migration is needed for Phase 14.0 because templates remain immutable server-side definitions.
- Handoff and licensing models are clearly documented before any code or export is sold.
