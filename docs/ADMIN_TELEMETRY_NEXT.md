# Next product telemetry: source-only scope review

Baseline: Editor main 57d8da0 (2026-11-14). No activation, Cloud composition,
IAM, credentials, private content queries or backfill is included in this change.

## Smallest compatible next producer

`hosted.security.project_recency` projects existing active-root status, generation
and server-managed updated_at, then returns fleet totals for current projects
modified within 24 hours, 7 days and 30 days. Windows are inclusive and fixed at
collection start. This is modification recency, NOT active users, sessions,
writing activity, words written, retention, or cumulative events. Rename,
membership and other non-writing changes can update the timestamp. A project
with several modifications is counted once, based on its latest timestamp.

The default-off injected producer makes at most four root RPCs: two projections
limited to101 roots and two limited COUNT timestamp fences. Over100 active
projects, missing/malformed/future timestamps, mutation, timeout or dependency
failure invalidates the whole observation. Empty populations require aggregate
observation timestamps, not an assumed zero. Payload has no project IDs, exact
per-project timestamps or text. Freshness900s; observation60s; absolute caller
deadline clamps every RPC. No collector is called or scheduled by this source.

Under a one-entry-per-matched-root index plan, planning cost is at most202 root
document reads plus two aggregate read units per successful collection. This is
an estimate requiring plan/pricing verification, not a hard dollar cap. Failed
attempts may incur reads. Future transport/cache/admission overhead must be added.
Existing30-per-month structural collection admission is NOT automatically shared
or expanded by this producer. Separate routes/requests must not double the budget.

Expanded access approval, before integration/activation, must explicitly cover
reading existing active-project updated_at, deriving these three fleet totals,
sharing them with owner-only Admin, retaining only aggregate observations in its
reviewed cache and the selected bounded manual refresh cadence. Do not count the
existing seven-count approval as covering this additional metadata category.

## Why task states and writing size are not simply connected

Task status is an editable private field. `private_firestore.py` seals all such
fields and expressly prohibits provider-layer queries/indexes on private fields.
Direct status COUNT queries would violate this boundary and undercount encrypted
rows. Keep Tasks state cards unavailable; no decrypt-to-aggregate shortcut.

A compatible future task-state producer would compute approved operational
buckets (open/later/done/archived) while canonical authorized mutations already
have the plaintext. Projections and task changes must commit atomically with
idempotency, generation fencing and lifecycle/undo/import/restore coverage.
New projects can start covered; existing projects remain unknown until a reviewed
backfill or complete canonical coverage proves every current task is represented.
A timestamp alone or observed new writes cannot prove historical completeness.

Current word_count fields are checkpoint/history summaries, not authoritative
current manuscript totals. Current words/characters need a specified manuscript
scope and tokenizer, computed at canonical block publication. Content must not
be read anew just for analytics without explicit approval. Deletes, moves,
imports, packed blocks, full replacement, undo/redo, recovery and project
publication must preserve contributions and coverage. Incomplete existing
projects must not appear as zero; content backfill would require separately
approved content processing with explicit caps and costs.

`security.metrics.DailyUsageMetrics` is validation-only, with no callers in src;
it is not evidence of a live event producer. Root updated_at and action journal
receipts cannot reconstruct sessions, active minutes or unique users. Those need
a separately designed event contract, retention and privacy review.

## Verification

Synthetic tests cover exact time boundaries, off/no client access, root-only
queries, complete-empty evidence, overflow, generation/population mutation,
invalid/future timestamps, deadline and stale observations. No live provider
calls, local heavy suites or runtime activation are part of this preparation.
