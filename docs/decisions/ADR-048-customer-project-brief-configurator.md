# ADR-048: Customer project brief before commercial quote

- Status: Accepted
- Date: 2026-09-21

## Context

The owner prioritizes a simpler customer-facing page for customer-owned Docker/Supabase deliveries, with one-time project pricing and separately requested integrations. The existing quote engine still models recurring charges. Displaying those estimates as this product's price would conflict with the requested flow. Requested features also include work not yet established as delivered.

## Decision

The public `/build/` page collects a four-step project brief: store, features, appearance/delivery, and review/contact. It does not display or calculate prices. A reviewed commercial quote remains the pricing authority; the existing estimate endpoint and inquiry estimate snapshot remain unchanged for compatibility. This revises the live-estimate presentation in ADR-040 without changing commercial authority or accepted records.

Existing configuration fields carry the selected server-owned template, format, product source, integration keys and delivery model. Bounded project notes carry explicitly requested features, languages, design and handoff preferences. These notes are untrusted requests, not entitlement grants, implemented capabilities or provisioning instructions. Factory operators must review them before issuing a quote. The post-submit Telegram link retains the configured contact destination but uses the inquiry reference and a scope-review message rather than the legacy estimate text.

The page defaults to customer server plus Supabase. Database integration is not offered as a second duplicate checkbox. Unknown APIs remain a distinct review request. Connection compatibility warnings preserve the customer's request without promising compatibility. The inquiry route still calculates its existing internal estimate; one-time commercial engine changes are deferred and must precede automated customer-owned quoting.

Only non-contact selections are saved in tab-scoped expiring drafts. Contact details, freeform notes, custom API text and secrets are not saved to browser storage. A successful submission clears the draft and freezes the submitted form. There are no schema, authentication, financial, deployment or provider changes.

## Consequences

Customers get a clear, reviewable brief without a misleading automated price or implied instant delivery. The factory sales console sees the requested scope through existing project notes. This is intentionally a review workflow; future automated packaging should introduce validated structured requirements rather than execute arbitrary notes. Existing recurring internal estimates are not approved one-time quotes. They require operator review until the pricing milestone is implemented.
