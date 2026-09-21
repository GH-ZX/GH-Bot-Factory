# ADR-049: Admin operations and customer-owned delivery scope

Status: accepted for implementation; production qualification and customer installation evidence remain pending.

## Decision

Customer-owned `supabase_cloud`, `dedicated` and `source_license` estimates have no mandatory factory subscription. Managed hosting retains its existing commercial behavior. Existing quote amounts are not rewritten. Platform operators create final quotes, whose immutable `scope_snapshot` preserves the inquiry configuration, structured brand/language/feature brief, notes and pricing model. Parent inquiry row locking serializes creation/acceptance; expired, superseded and conflicting acceptances fail closed. Tenant roles never gain platform authority.

The Admin interface shares the public builder's cream and green identity. Tenant care and marketing use `/admin/operations`; customer-owned support APIs use `/storefront/support`. All six new tables carry `tenant_id`: `support_cases`, `support_messages`, `coupons`, `coupon_redemptions`, `announcements`, `announcement_deliveries`. Export/import includes these records. Destination restores cancel pending broadcasts and retain interrupted sends as unknown rather than replaying them.

## Accounting and warranty

Coupons are tenant-scoped percentage discounts, limited by currency, minimum spend, expiry and maximum uses. Checkout locks the coupon and conditionally increments its counter; the database constrains the counter and uniquely binds redemption to an order. The normalized coupon code participates in the checkout request fingerprint. Each unit discount rounds down to cents and keeps payable units at least 0.01. The original pricing quote remains unchanged; order items and economics record the actual discounted sale, while a redemption records the discount. Debit, order, redemption and durable fulfillment commit together through the existing LedgerService checkout transaction. Retries never consume another coupon use. Refunded orders retain the historical redemption and do not restore coupon availability.

Warranty duration and terms are captured on each order item at purchase. Existing sales have empty terms and do not gain retroactive warranty coverage. A claim requires an owned, fulfilled order, an eligible item, and an unexpired purchase-time warranty (duration begins at order creation). A unique item claim prevents duplicate claims. Cases use row locking plus version comparison for conflicting replies/decisions. Staff can handle conversations; OWNER/ADMIN decides warranty approval/decline. Approval is a recorded manual decision, not a supplier purchase or wallet mutation. Operators perform any financial resolution through existing controlled workflows and record its outcome in the case.

## Announcements

Operators save a plain-text draft and explicitly queue it after review. Audience is snapshotted from tenant bot bindings, with an optional fulfilled-buyer filter; campaigns require 1–10,000 recipients. The worker rechecks tenant activity, membership, bot activity and blocked status before sending. Delivery claims commit before network calls. Explicit Telegram rate-limit rejection can be retried after its requested delay; definitive rejection is failed. Transport ambiguity and interrupted sends become UNKNOWN and are never automatically repeated. Campaign summaries converge to COMPLETED or NEEDS_REVIEW, including after a worker crash. Cancellation stops pending deliveries, but cannot retract an in-flight message. No real messages are sent by automated tests.

Installation-wide worker queue discovery is the same documented exception used by existing workers. Every related record read and mutation is rebound to the discovered tenant. Telegram tokens are resolved only through SecretStorage and never logged. The current implementation sends one message per worker cycle, avoiding unnecessary bulk load.

## Boundaries and follow-up

Migration `0ce5d2c98e7f` adds the six tables, category/product display order, quote scope and purchase terms. Temporary database defaults backfill existing rows and are removed afterward. Product image URLs require HTTPS without embedded credentials; warranty metadata is validated at the admin write boundary.

This milestone implements tenant-side controls. Shopper-facing support/coupon entry, full Telegram/MiniApp bilingual polish, delivery-package/report automation and production release qualification remain deferred to steps 4–6. The final Supabase/VPS installation test must use an actual customer later (step 2). Canonical verification is required, but it is not evidence of a production release or real-customer acceptance.
