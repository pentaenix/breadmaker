# Current structural product inventory

`hosted.security.product_inventory.GoogleFirestoreProductInventoryProducer` is
an explicitly injected, **default-disabled** source producer. It creates no
clients, credentials, services, schedule, endpoint or storage. Live activation
and publication require Cloud composition and a reviewed consumer. This is not
part of the existing two-count inventory wire contract.

The version 1 payload has source `cloud_product_inventory_v1`, UTC `as_of`, a
`coverage` observation window, `complete`/`unknown` completeness, `non_atomic`
consistency, and exactly these metrics:

- `current_cloud_projects`
- `current_chapters`
- `current_scenes`
- `current_canvases`
- `current_tasks`
- `current_story_maps`
- `current_reviews`

Each count means extant direct root records under explicitly active project
roots. Archived tasks/reviews remain included. These are not human activity,
lifetime creations, published books or counts of nested nodes/versions. Retired,
deleted, staging and missing-status projects are excluded, as are orphan child
collections without a current project root. No document text, task status,
review content, title, author identity or manuscript body is retrieved.

The only document query projects `status` and `generation`. Six fixed child
collection COUNT aggregates follow for each eligible project. A second projected
root query must reproduce the entire project-ID/generation map. Canonical
mutations advance the project generation, so concurrent edits, removal or
publication invalidate all totals. This is a non-atomic observation, not a
historical snapshot; an edit after collection ends is covered by the timestamp
and freshness limit, not by a claim of ongoing accuracy. No failed or incomplete
run becomes zero. Empty eligible populations become known zero only after both
root queries succeed. Consumers must enforce the 15-minute freshness limit.

## Existing projects and future metrics

These record-existence counts need no content backfill. Existing active roots
with valid generations are queried identically to newly created ones. Missing or
invalid generation metadata yields unknown rather than seeding an invented
baseline. No database records are changed by collection.

Words, characters, logical manuscript blocks (including packed layouts), page
estimates, activity minutes and feature sessions are deliberately absent. They
need separately reviewed authoritative write-time metadata, generation binding,
coverage markers and an honest legacy backfill strategy. This producer does not
supply those metrics or repurpose their missing data as zero.

## Bounded operations and budget

Each run makes two root queries plus six aggregate queries per eligible project:
no more than 602 RPCs. Each root query limits its result to 101 projected rows;
101 means over-cap, not a truncated usable total. Each child query is limited to
10,001 records before COUNT; a count above 10,000 invalidates the result. Each RPC
has retries disabled and at most five seconds, and the whole collection has a
60-second monotonic deadline. All streams are closed. Any limit or provider
failure yields unknown. These are work bounds, not a dollar-spend guarantee;
provider pricing and existing total-app spend must be checked before scheduling.
There is no timer or automatic retry. Keep this disabled until a refresh cadence
and Cloud-side budget enforcement fit the owner's total application budget.

Synthetic tests are in `tests/unit/test_product_inventory.py`. Run them in CI;
no live provider or author data is used. Source delivery does not mean that
production metrics are connected, and CI results must be checked separately.
