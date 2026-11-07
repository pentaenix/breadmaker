# Large manuscript revisions: implementation and activation plan

Status: **inactive reader/publication experiments, not a production feature**. The 10.5.95
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

The synthetic suites now also cover an inactive text-only resumable publisher
(`revision_staging.py`), including a 100,000-word manuscript, one-pointer
replacement/Undo, append, one-record large chapter deletion/Undo, cancellation,
interruption, missing actor contexts, private-root rejection, malicious valid-hash
plans, cross-project object pointers, and account/owner locks. A committed result
retains its original Undo token; current availability is reported separately.
Empty structural rollback closes only its action without mutating the project.

Packed annotations now use the same source visibility policy. Existing missing
sources remain stale/orphaned; physically present retired or tombstoned sources
are hidden. A future GC must preserve that distinction rather than resurrect old
annotations by removing their last source marker.

Current chapter/block reads have an indexed, bounded membership-query prototype:
61 live chapters require six logical provider reads, unchanged with 40,000
retired/tombstoned blocks. This is synthetic query-count evidence, not production
latency. Root-only compatibility is implemented for account closure and narration
GC. Image helpers are separate and still require caller integration; see
`HOSTED_REVISION_IMAGES_EXPERIMENT.md`.

These tests do not prove runtime integration, every operator's rollback behavior,
or production performance. No runtime constructs these services.

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
   rewriting them. Packed annotation visibility is implemented, but editing-search
   status/count and other direct consumer paths still require integration review.
   Never silently filter a physical inventory through this view.

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

## Capacity and deployment scope must remain explicit

Visibility does not reduce physical storage. A current 10,000-record novel and
one retained replacement exceed the 12,000-row **experimental encrypted recovery**
catalog budget. That scanner is not the normal hosted-v3 archive path, and the
revision service rejects private/encrypted projects. The 25,000-entry physical
census/manifests are operator preparation infrastructure, not the current HTTP
project-delete implementation. Their production purge gate has no allow branch.

Current project deletion is recoverable root tombstoning. Production account
closure scans roots/members, closes identity, and explicitly leaves physical purge
pending. Its critical format-compatibility requirement is root-state decoding,
which must never mask erasure. Ordinary hosted-v3 archives have their own 64 MiB
limit; the supplied staging archive CLI also caps physical collection reads at
20,000 rows. Current production archive composition has not been verified here,
so those CLI limits must not be described as live production behavior.

Repeated retained revisions still create genuine storage growth and future
physical recovery/deletion work. The new live block/chapter reader excludes old
rows by indexed current-head/chapter queries, but physical inventories must remain
complete. Do not feed filtered live counts into a deletion proof. Existing
`erasure_census.py` already consumes provider pages at one consistent read time;
adding another page token or raising a constant is not a complete resumable proof.

Before destructive revision GC, establish project-scoped pins for active heads,
all restorable action before/after/current references (including departed actors'
journals), staged upload attempts, History/Review/Checkpoint dependencies, trash,
and holds. Claim collection durably before deletion and prevent new references.
A delayed create-only upload can recreate an orphan after generation-CAS deletion;
use an explicit attempt/quiescence or same-key fence protocol rather than assuming
that two inventories prove uploads have stopped. No such GC is enabled here.

A future read-only resumable physical inventory must bind exact database, scope,
root freeze, read time, protocol and expiry. Persist authenticated immutable page
shards before CAS-advancing a trusted checkpoint; resume by job ID, not arbitrary
caller cursors. Validate ordering, uniqueness, unknown descendants and missing
parents before declaring completion. Streaming manifest consumers must replace
current all-entry materialization. GCS version/soft-delete inventory and upload
quiescence are separate proofs. Inventory completion never authorizes purge.

Keep both present product limits and future recovery limitations explicit. Do not
claim unrestricted full-size support or physical deletion on the strength of an
inactive reader or synthetic pointer-publication test.

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
