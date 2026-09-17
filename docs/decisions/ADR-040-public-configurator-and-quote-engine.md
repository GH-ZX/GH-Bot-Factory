# ADR-040: Public Configurator, Server-Authoritative Quotes, and Rate-Limited Inquiries

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

Following the Phase 14 product vision and Phase 14.0 template guidance (ADR-039), Phase 14.1 introduces the prospective customer discovery and configuration flow.

Critical constraints:
1. **Domain Isolation:** The apex domain `gh-store.me` hosts a live external store and must never be altered or bound. GH-Bot-Factory is deployed on its assigned subdomain (`botfac.gh-store.me`). The public configurator is mounted at `/build/` on this host.
2. **Authoritative Pricing Law (Law 6):** Client browsers are untrusted presentation surfaces. Prospective customers configuring their bot must receive server-authoritative estimates calculated by the platform quote engine, not fabricated in client-side JavaScript.
3. **Secret Safety Law (Law 3):** Public inquiry forms must never request, accept, or persist bot tokens, provider API keys, or payment credentials.
4. **Abuse Resistance:** Public endpoints must be rate-limited and protected against automated spam.
5. **Manual Review & Telegram Contact:** Automated self-provisioning is intentionally out of scope for Phase 14.1. The flow produces an inquiry record and an immediate Telegram chat link with a pre-filled summary.

## Decision

1. **Subdomain-Mounted Public Configurator:**
   - The configurator web application is hosted in `apps/build/static/` and mounted at `/build/` on `botfac.gh-store.me`.
   - Security headers middleware applies strict CSP, cache-busting, and no-sniff policies to `/build/`.

2. **Server-Authoritative Quote Engine (`packages/marketplace/quotes.py`):**
   - The quote engine calculates itemized one-time setup fees and recurring monthly costs based on:
     - Product format: `bot` ($49 setup, $29/mo), `miniapp` ($69 setup, $39/mo), `combo` ($89 setup, $49/mo).
     - Product source: `stored` ($0), `provider_api` ($15 setup, $10/mo), `hybrid` ($25 setup, $15/mo).
     - Delivery model: `managed` ($0 setup premium, standard monthly), `dedicated` (+$450 setup, +$50/mo), `source_license` (+$2,000 one-time, $0/mo).
     - Add-on integrations: (e.g. 5sim / SMS: $30 setup, $15/mo; Crypto Gateways: $25 setup, $10/mo; Custom API: $50 setup, $20/mo).
   - `POST /api/v1/public/estimate` takes desired specifications and returns an immutable itemized breakdown.

3. **Public API Router (`apps/api/v1/public_marketplace.py`):**
   - `GET /api/v1/public/templates`: Returns public template catalog with guidance metadata from Phase 14.0.
   - `GET /api/v1/public/integrations`: Returns available integration add-ons and capabilities.
   - `POST /api/v1/public/estimate`: Validates options and calculates authoritative quote.
   - `POST /api/v1/public/inquiries`: Captures lead information, persists a `CustomerInquiry` record with server-estimated quote snapshot, and returns a pre-filled Telegram deep-link.

4. **Durable Customer Inquiry Persistence (`packages/marketplace/models.py`):**
   - Model `CustomerInquiry` stores:
     - `id`: UUID primary key
     - `contact_method`: `telegram`, `whatsapp`, or `email`
     - `contact_handle`: User's username, phone, or email
     - `project_notes`: User's project description
     - `configuration`: JSON snapshot of chosen specs
     - `estimated_quote`: JSON snapshot of server-calculated itemized quote
     - `status`: `NEW`, `REVIEWED`, `CONTACTED`, `ACCEPTED`, `REJECTED`, `ARCHIVED`
     - `ip_hash`: SHA-256 hash of client IP for audit and abuse tracking
     - `created_at`, `updated_at`
   - Stored in table `customer_inquiries` with migration `g1a2b3c4d5e6`.

5. **Modern, Responsive Frontend (`apps/build/static/`):**
   - 6-step guided wizard: Format -> Template -> Product Source -> Add-on Integrations -> Hosting Model -> Review & Inquiry.
   - Real-time quote preview reflecting server calculation.
   - Direct CTA: "Send Project Inquiry" + "Chat with Owner on Telegram" with URL-encoded inquiry details.

## Consequences

- Prospective customers can explore templates, calculate costs, and contact the Factory Owner without leaking secrets.
- Client browsers cannot manipulate pricing; accepted inquiries snapshot authoritative quotes.
- Apex domain `gh-store.me` remains untouched; the configurator operates safely on `botfac.gh-store.me/build/`.
- The Factory Owner retains full control over project acceptance and tenant provisioning.
