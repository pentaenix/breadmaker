# Manuscript revisions: operator and recovery boundaries

This note describes source compatibility and synthetic evidence. It does not
activate archive jobs, destructive purge, retention GC, or a production rollout.

## Active product-state portability

Hosted-v3 ZIP, hosted JSON, and local Schema-42 packages read the normal
manuscript view. They export the published chapters/blocks and visible assets;
staged and retired rows stay in the physical store. Top-level `_bm_` metadata,
including revision heads and tags, is removed. Imports publish a fresh ordinary
project and reject supplied reserved revision fields. Pre-existing shared assets
remain available under their existing History/Review semantics.

The separate staging-only `recovery_writer_cli` now composes this normal reader
above its project-scoped, read-only adapter. Indexed chapter membership queries
retain the adapter's existing result limit. This fixes source compatibility for
active product-state snapshots; it neither enables the job nor turns its output
into a full physical backup. Its 64 MiB archive cap and per-query 20,000-row cap
are source contracts of that staging CLI, not verified production runner limits.

## Physical inventories and lifecycle

`manuscript_lifecycle.read_project_lifecycle_status` validates known physical root
states without rewriting the stored root. Account closure keeps the format
marker while writing literal `erasure_pending` plus its operation. Root state
compatibility never overrides membership, account fences, retention or holds.

The operator purge planner removes only exact action-journal/manuscript wrappers
inside its constructor. It stops at the lifecycle fence. It does not expose an
HTTP switch or change the sandbox executor's exact-memory-driver requirement.
A separately supplied physical provider is also supported. A manuscript view
buried beneath a lifecycle fence is rejected instead of removing that fence.

Trash previews include physical staged/retired rows. Exhaustive discovery still
includes unknown descendants and missing parents. Root checks decode only for
state decisions; comparison, revision tokens and inventory digests keep their
physical representation. A preview is never purge authority.

## Recovery limitations that remain

There is no supported full retained-revision recovery path in the reviewed
Editor source. Ordinary hosted-v3 archives normalize active state. They do not
preserve all tab-head metadata, retired content, abandoned staging jobs, immutable
source objects, append-only asset-publication receipts or account-scoped Undo
journals. Neither a successful portable
round trip nor an active-only archive proves those retained records recoverable.

Accordingly, format-v1 projects fail closed in purge preparation, even when an
active-only archive exists. Receipt commit rechecks the format to close adoption
races. The memory-only purge executor also rejects such roots. The experimental
sealed recovery source explicitly rejects physical format-v1 roots before
publishing recovery output; its portable reconstruction cannot preserve the
revision protocol. Revision writers already reject private/encrypted projects.
The historical encrypted wrapper around ordinary product-state packages still
has the same active-only coverage as those packages; encryption is not evidence
of retained-revision or complete account recovery.

The 12,000-row private recovery catalog and 25,000-entry physical census limits
are unchanged. They do not bound ordinary active-only product exports. Repeated
retention still grows physical storage; there is no new GC, resumable complete
physical recovery manifest, or upload-quiescence protocol here. Current project
trash and account closure preserve retained data rather than physically purging
it. Enabling physical deletion requires separate complete recovery/retention
proofs and authorization.

## Verified production safeguards, 2026-11-07

The separately reviewed Cloud main commit `7e275fcc` matches release workflow run
`42399584852`, deployed on November 7. Its
`infra/modules/production_stack/main.tf:40-65` hard-disables all archive/recovery
jobs. Read-only inspection of production in `us-central1` found only the account
closure and narration-GC jobs, with no independent archive writer running.

The production `(default)` Firestore database has PITR enabled. The observed
oldest recoverable version was `2026-10-31T19:43Z`; this is a moving provider
window, not a permanently pinned recovery point. Content/assets buckets have
versioning and 30-day soft deletion. The recovery bucket has versioning, 30-day
retention and 30-day soft deletion. These raw-storage safeguards preserve more
than an active-only portable package and may preserve the physical revision
state and immutable objects needed for recovery.

No live full retained-state restore was verified. A rehearsal must select a
consistent Firestore point and matching immutable object generations, include
publication receipts, jobs and relevant Undo authority, and prove recovery into
an isolated target before claiming full revision/account disaster recovery.
Product-format restore incompatibility must not be conflated with absence of
raw provider safeguards, or with a proved source-loss restore. This compatibility
change performed no Cloud mutations and supplies no rollout approval.
