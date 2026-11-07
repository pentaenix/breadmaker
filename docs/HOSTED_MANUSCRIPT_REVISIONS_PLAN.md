# Large manuscript revisions: implementation and activation plan

Status: **inactive reader experiment, not a production feature**. The 10.5.95
limits in `HOSTED_IMPORT_RECOVERY_LIMITS.md` still apply. No application/runtime
constructs the experimental reader, no project is upgraded, and no import or Undo
limit has been increased. A passing reader test is not evidence of a complete
publication, recovery, or erasure protocol.

## Why publication must change

An existing-project replacement currently journals the before/after metadata of
every changed row in one Firestore transaction. Raising the journal limit cannot
make a novel-sized replacement atomic. Staging ordinary rows without a shared
reader also exposes incomplete content through direct chapter, highlight, asset,
narration, or export reads.

The proposed representation keeps canonical project IDs and object keys. Each tab
has a server-owned active revision token. Chapters carry that token; blocks derive
visibility from their chapter. Legacy missing tokens match each other, so the old
novel need not be copied just to create a recovery point. Replacement stages new
invisible rows and finally changes the tab pointer. Append stages beneath absent
tabs and publishes their tab records together. A large chapter deletion changes
one chapter tombstone. These small publication changes, rather than all prose
metadata, are the existing structural journal's before/after images.

Prior rows and immutable objects remain available to the narrowly authorized
Undo implementation. This is **structural Undo**, not activation of hosted
History Restore or Checkpoint Apply; both currently fail closed independently.

## Experimental shared reader

`src/hosted/manuscript_visibility.py` implements an uncomposed candidate facade:

- Point, batched point, collection, collection-group, count, and transactional
  writes use the same visibility rules.
- Staged/retired chapters, blocks, tab-level scenes, linked annotations, and newly
  staged assets are hidden from normal reads. Old shared assets stay available to
  pre-existing History/Review references.
- Normal writes cannot create publication markers, mutate hidden records, or
  attach new blocks to a retired chapter. Newly created chapters inherit the
  active tab token. These checks happen before any provider write.
- A separate retained view supports trusted staging and journal recovery. It
  keeps the same account-lifecycle store underneath; it is not an erasure bypass.
  No request parameter may select that view.
- A physical project root format marker encodes both active and trash states as
  `active_manuscript_v1` and `deleted_manuscript_v1`. A compatible reader decodes
  them to normal lifecycle states. Version 10.5.95 refuses those states. Encoding
  only active would be unsafe: an old binary could restore ordinary trash into
  an ordinary active project and expose retired rows.
- The codec rejects unknown/missing/mismatched markers and preserves the format
  on subsequent root writes. It never masks `erasure_pending` or an erasure
  operation. The marker is not an authorization grant.

The initial synthetic suite covers these contracts. It does not prove a runtime
integration, old-build compatibility across every operator, or performance.

## Integration gates before enabling any writer

1. **Reader composition and compatibility.** The candidate normal view sits beneath
   the action-journal facade and above the account-lifecycle write fence. The
   journal must call the view’s transaction-local `prepare_mutations` hook before
   capturing after-images, so derived revision tags exactly match persisted rows.
   Normalization and validation are separate passes over the complete write
   overlay; block-before-chapter order must not change the result. All runtime
   services must share it, including raw chapter saves, annotation packs,
   narration sources, read-bundle cache attachment, assets, exports, Reviews,
   package generation, and browser navigation. Exercise legacy projects without
   rewriting them. Packed annotation payloads currently return hidden-source rows
   marked stale; they need explicit per-source visibility filtering. Never silently
   filter a physical inventory through this view.

2. **Root-only operator codec.** Account closure (`account_closure.py`), backup and
   recovery (`recovery_coverage.py`, recovery writer/planner), project trash,
   narration GC, and operator compositions need known-format root decoding with
   unfiltered physical children. Unknown formats must fail closed. Census,
   deletion receipts, and purge still account for every staged and retained row
   and immutable object. Their inventory digests must use a consistently defined
   physical representation, not a mix of decoded and physical roots.

3. **Journal retained view.** `ActionJournal.restore` needs raw retained product
   rows, while preserving existing membership, expiry, root-generation,
   head-chain, record-image, and ABA checks. Its parent-chain rebase examines the
   prospective restored revision, not just the currently visible one. Do not add
   permissions, memberships, receipts, or root authority to the restorable set.

4. **Resumable authorized staging.** Create a durable actor/project-bound job with
   the expected project generation, operation fingerprint, destination, source
   digest, plan pointers, expiry, and explicit replacement consent. Every bounded
   step must recheck membership, source generation, erasure, receipt ordering,
   and object integrity. Use verified immutable prose and validated image data.
   The final transaction verifies the job/receipts and publishes only tab pointers
   and bounded ordering changes. Replay after Undo or an intervening edit fails.
   Do not keep a novel-sized HTTP request or process-local job alive.

5. **Asset/reference semantics.** New asset metadata must remain hidden until a
   referencing revision is published. Deduplicated assets shared by existing
   tabs, History, and Reviews cannot be retired just because one tab changes.
   Native export/import must normalize internal revision metadata to a complete
   active portable project without importing authority or stale revision tags.

6. **Retention capacity and reclamation.** Resolve the capacity issue below before
   writer activation. Retain every revision reachable from an unexpired Undo
   chain or another recovery/history reference. GC needs a durable, idempotent,
   generation-checked mark-and-sweep protocol, not best-effort deletion after an
   HTTP response. Project/account erasure must still include abandoned jobs,
   invisible metadata, all retained revisions, and orphan immutable objects.

7. **Two-phase rollout.** First deploy reader/operator compatibility with writers
   disabled and verify it. Only then activate the writer after full product and
   deletion/recovery rehearsals. A format-enabled root must remain fail closed on
   an unaware rollback. The release/rollback workflow must explicitly recognize
   the minimum reader version and avoid reporting an unavailable upgraded project
   as a healthy rollback.

## Capacity is presently an activation blocker

Visibility does not reduce physical storage. A current 10,000-record novel and one
retained 10,000-record replacement already exceed the 12,000-row recovery catalog
budget. Three such imports can exceed the 25,000-entry physical census budget.
The census also budgets RPC calls and elapsed time. It already consumes provider
pages at one consistent read time; merely adding another page token or raising a
constant is not a complete resumable census protocol.

Affected boundaries include `recovery_coverage.py`, provider
`project_document_inventory`, `purge_planning.py`, and `erasure_census.py`. Normal
navigation/bundle gates must count live rows; deletion/recovery proofs must count
all physical rows. These are different questions and must never share a filtered
answer. Repeated collection scans over all retired rows would also make navigation
slower as history grows, contradicting the latency goal.

Before activation, implement indexed/paged live reads plus retention-aware
reclamation, and a bounded resumable physical inventory with durable cursor and
snapshot authority (or another reviewed storage representation that avoids this
unbounded accumulation). A valid cursor must preserve exact database/project,
read time, root freeze, traversal stack, path set/digest, unknown-descendant
coverage, and completion proof across crashes. An expired snapshot must restart;
a partial scan must never authorize purge. Concurrent writers, membership changes,
actor erasure, duplicate pages, token cycles, and provider failures require tests.

A deliberately stated retained-storage quota could be a product choice after
review, but a hidden small cap is not full novel support. Until this is resolved,
the existing fail-closed bounded operations remain the safe production behavior.

## Required end-to-end evidence

- Novel-sized replacement, append, and chapter deletion with two independent
  runtime replicas, refreshed browser state, Undo/Redo, and chained earlier edits.
- Interruptions before/after each durable plan, batch, object, receipt, publication,
  journal finish, and cleanup stage. Replays must be safe and bounded.
- Stale saves, membership revocation, project trash/restore, account freeze and
  erasure locks during every stage; no authority is restored by Undo.
- Staged IDs and asset URLs cannot retrieve unpublished content. Retired IDs cannot
  be mutated by entity, chapter-save, highlight, cache, or narration paths.
- Verified object corruption fails closed; imports with images preserve their
  references; package/DOCX export contains exactly the active manuscript.
- History/Review remains readable under its own authorization. Structural Undo is
  never represented as durable hosted History restore.
- Repeat full-novel replacements beyond the current inventory ceilings, then
  complete recovery, retention GC, project deletion, and account-erasure rehearsals
  including unknown nested records and descendants of missing parents.
- Old-reader rejection after active-to-trash-to-restore transitions, plus a release
  rollback rehearsal. No live user manuscript should be used as the test fixture.
