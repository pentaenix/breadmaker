# Bounded mixed manuscript reads

The optional whole-book accelerator now asks the transaction adapter for a
bounded snapshot rather than materializing every block before checking size.
The Google adapter limits the query to 10,001 results, consumes snapshots
incrementally, and closes the stream on completion, overflow, or error. The
memory adapter copies one matching row at a time. Unsupported adapters and
lifecycle transactions with pending changes conservatively use ordinary paging.

The existing authorized membership check and 10,000-record count check remain.
All fetched document bodies and metadata have an aggregate 8 MiB UTF-8 JSON
budget. A single provider document can be decoded before crossing the budget;
Firestore's own document limit still applies. This is a serialized-content
budget, not a claim that Python heap use equals 8 MiB. Oversized collections do
not create an unbounded in-memory body list. Filtering chapters happens after
the bounded read, so unrelated chapters can conservatively trigger paging.

Canonical unencrypted inline blocks need no object read. Canonical pointer
blocks still require valid immutable read-bundle associations. Stale or missing
accelerators and flat legacy pointers retain the ordinary paged path. Encrypted
stores never use this plaintext fast path. Corrupt bundles remain errors.

The decoded bundle budget includes the previously collected document bytes.
Existing compressed-input, per-bundle decompression, bundle-count and response
budgets remain. Current authorization and generation checks around HTTP book
publication are unchanged. No persistent cache or content representation is
created by reading.

Focused coverage includes bounded provider consumption and stream closure,
lifecycle pending writes, mixed inline/object text fidelity, all-inline stale
accelerator avoidance, oversized inline documents, absent adapter capabilities,
legacy fallback, existing corruption checks, and authorization/revocation races.
Full release verification remains required before deployment.
