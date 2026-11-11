# Subscription foundation (disabled)

This phase prepares account settings without choosing a provider or a price.
No checkout, webhook route, subscription mutation, provider SDK, credential,
network request, infrastructure permission or payment activation is included.
An environment variable cannot activate this implementation.

`hosted.billing.DisabledBillingService` is the only shipped adapter. The existing
Identity Platform authenticated, no-store account/security snapshot adds an
explicit unavailable billing object. It does not claim a free plan, zero revenue
or absence of an actual provider subscription. Local accountless editing stays
unchanged. Account & security reuses its existing section/header/empty-state
classes and modal, with no new CSS, controls, provider URL or account data.
All seven locales explain that billing is not connected.

`PaidPeriod` is an immutable trusted-server domain input, not an HTTP schema or
current application authorization mechanism. Its evaluator matches the server
account and uses aware UTC timestamps with an exclusive end. Cancellation stops
renewal, preserving already-paid access until that end. Failed renewal must never
extend the paid period. Verified refund/dispute policy can explicitly revoke it.
No app route currently obtains paid periods or grants paid access from this class.

The aggregate dashboard seam stays unconnected, with unknown timestamp/coverage
and no financial figures or customer information. Future adapters must group by
currency, distinguish gross sales, customer tax, fees, refunds, accrued net, MRR
and settled payouts, and preserve disconnected/stale evidence in the Admin UI.
It is not connected to the Admin deployment in this phase.

## Remaining implementation gates

Confirm currency, price, paid features/quotas, provider approval and legal/tax
setup. A later adapter must bind server-created checkout intents to authenticated
accounts, prevent duplicate subscriptions, validate raw-body webhook signatures,
deduplicate and reconcile out-of-order events transactionally, separate test/live
stores and allowlist plan/variant IDs. Browser redirects cannot grant access.
Authenticated short-lived portal URLs must never enter logs or Admin projections.
Cancellation and paid-period dates require verified provider state; data deletion
is a separate process. No keys or new permissions may be introduced silently.

The current Manuscript shell and shared component catalog were inspected; the
closest analogue is the existing Account & security section. Tests render the
actual fragment and styles at 320/900/1280 widths in light/dark and seven locales.
The existing modal's navigation/authentication lifecycle is not changed.

Run the focused unit/route-body and browser tests, then full `./verify-release`
and paired Cloud gates before release. Focused results are not full release or
production acceptance. Provider sandbox, live payment and cancellation flows
cannot be tested until an adapter and separately approved setup exist.
