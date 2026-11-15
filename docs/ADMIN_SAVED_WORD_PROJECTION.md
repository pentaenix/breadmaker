# Saved-word projection foundation (not connected)

## Retention and deletion preparation (default off)

The enabled coverage guard deletes an existing `admin_saved_word_coverage/{project}`
ledger atomically when a transaction physically removes an existing project root.
Soft deletion still invalidates coverage. A failed transaction preserves both
records. Deleting an already absent root is a no-op and does not clean up an
orphan ledger; such a ledger requires separately reviewed cleanup.

Project purge and memory-only account preflight have an explicit
`saved_word_retention_enabled=False` option. Default project preflight performs
no new saved-word ledger/policy reads and preserves its existing fingerprint.
Opt-in project preflight reads and fences the exact project ledger and canonical
shared policy at `admin_operational/saved_word_policy_v1`. It lists them separately
from the project subtree and blocks existing subtree-only executors when cleanup
or policy review is needed. It never adds shared policy to deletion candidates.
Memory-only account preflight inventories owned/staged/purge-audit-linked ledgers
and nested policy identity references; unattributed orphan ledgers and malformed
policy schemas block an erasure-complete claim. References require explicitly
reviewed policy-entry removal, not deletion of the shared policy document.

These are synthetic preparation boundaries, not a production erasure executor.
Runtime composition must enable retention census with the metric before any live
activation. Existing snapshots/cache copies, audit receipts, independent recovery
archives, provider backups and previously exported aggregates are not erased by
root-ledger cleanup. Their retention/invalidation requires separate integration
and authority; this patch makes no claim of complete historical data erasure.

### Exact-memory purge composition proof

A second explicit option, `saved_word_memory_purge_enabled=False`, is accepted
only with enabled retention census, the exact raw memory document driver, and
its exact memory discovery adapter. It permits the existing memory-only purge
rehearsal to carry the inventoried project ledger through its existing owner,
tombstone, retention, independent archive, preparation receipt and manifest gates.
It does not make a Google/live executor available or unwrap unknown store types.
Policy references still block preparation and are never removed by this path.

The receipt pins both ledger and shared-policy presence, document revision and
data fingerprint; it does not copy policy identities into another retained
snapshot. Every resumed destructive step checks those pins, including absent
sidecars that appear later. Synthetic object deletion holds the memory document
lock while checking sidecars and deleting its exact generation. Final root,
owner membership, existing ledger and progress receipt change in one enclosing
memory transaction through the same guard logic used by canonical saves. A
failure deleting the ledger rolls all four changes back. Default-disabled
manifests and inventory fingerprints remain unchanged. This memory-lock proof
does not establish production cross-provider atomicity or production authority.

This source is unwired and default-off. No live access or initialization has been
performed. The pure projection functions make no provider calls; the separately
opt-in transaction adapter and metadata initializer are described below. This is
not a connected Admin producer or a content-backfill implementation.

`count_saved_text` counts already-authorized saved plaintext using the existing
History/checkpoint Unicode regex `[\w’'-]+`, version `saved_text_unicode_words_v1`.
This differs from the visible editor's whitespace count; even punctuation-only
hyphen runs count under this existing definition. No text or digest is retained.
The function must run in canonical App saves before encryption, never in Admin.

`prepare_complete_snapshot` prepares a bounded candidate for a full verified
canonical manifest. Empty input is zero only when the writer has verified the
entire current manuscript is empty. The function cannot itself verify coverage.
`prepare_replacement` prepares a revision-fenced delta for known complete data.
Unknown coverage cannot become complete after a subset edit. Disabled interfaces
return unknown without consuming the contribution iterable.

## Required integration before activation

- Atomically commit contribution metadata, aggregate and content revision with
  canonical writes; operation replay must return before applying a delta.
- Cover domain create/update/delete, chapter replacements/deletions, native and
  document imports, encrypted live restores, Undo/Redo/rollback and fallback.
- Import/restore candidates stay staged until complete manifests are validated
  and publication commits. A failed operation must not affect the current total.
- Missing contributions, unsupported paths or mismatched revision invalidate
  coverage. Initial zero is allowed only at verified new empty project creation.
- Legacy coverage requires separately approved bounded content processing. A
  resumable scan must verify canonical generation before publishing; edits during
  scans invalidate or restart it. No implicit telemetry decryption is allowed.
- Validate actual removed block identities/contributions inside the canonical
  transaction. These pure arithmetic interfaces cannot prove input provenance.

## Attribution and reporting decisions still outstanding

Proposed scope is current saved manuscript text across all books in ACTIVE Cloud
projects, excluding history/cuts/annotations/title fields and unsaved UI text.
ACTIVE-only versus all retained manuscripts remains a product decision.

Exclusions should initially affect only the new word metric, not silently alter
existing stock, recency or map-line feeds. Membership owner is a hosted usr_ ID,
not Identity Platform UID. Resolve issuer+subject through the authoritative
identity binding before deduplication, and fail closed on mapping/incarnation
ambiguity. Multiple provider identities may represent one hosted owner.

A future aggregate must state count definition, eligibility scope, policy scope,
immutable exclusion revision, observation window and coverage. All/included/
excluded totals remain unknown when coverage/ownership cannot be proven. Admin
receives no manuscript content. Policy changes invalidate the old filtered
snapshot and do not automatically scan or reset the shared refresh allowance.

## Verification

Synthetic tests cover tokenizer examples, disabled lazy inputs, unknown coverage,
create/edit/delete/Undo arithmetic, revision conflicts and invalid/negative data.
These are foundation tests, not proof of canonical transaction integration.
No runtime switch, persistent metadata write, live scan, IAM or credential is
introduced by these files. Any integration requires reviewed synthetic transaction
and recovery tests plus normal exact-head CI before a separate activation decision.

## Uncomposed invalidation guard

`saved_word_guard.SavedWordCoverageGuard` is an explicit opt-in transaction
adapter, not installed by runtime. When placed below ActionDocumentStore it sees
canonical writes and journal restoration without changing the root-authority
allowlist or inserting telemetry in Undo records. It conservatively handles an EXISTING ledger on changed project roots, blocks
or chapters; mutation callbacks never create a ledger or promote coverage. The
revision-bound maintenance extension and separate initializer are described below. Missing ledger remains unknown, including import/restore
publication. Identical writes and receipt-only replay do not update the ledger.
All reads precede writes, and invalidation shares the canonical transaction.

This uncomposed adapter must not be treated as a live counter until the complete
writer contribution and initialization integration is reviewed. Read cost when explicitly enabled: one ledger lookup
per affected project per transaction attempt, plus one root lookup if not already
staged; one ledger write when it exists. Buffering captures before-images and can
add document reads per modified record. Retries multiply these costs. The wrapper
must therefore not be silently enabled for production saves. Disabled delegates
straight through with no added reads or writes. Content backfill remains absent; the later explicit metadata-only initializer
is described below.

The optional `HostedDomainStore(saved_word_projection_enabled=True)` injection
now prepares a server-owned `_bm_saved_word_v1` block contribution from plaintext
already present during ordinary create/update and chapter replacement. Bootstrap
never passes this option. Omitted/oversized plaintext produces unknown metadata;
it does not trigger an extra content read or reject an author's save. Existing
unchanged blocks do not gain coverage. Contributions travel in canonical journal
before/after images, so Undo/Redo restore them with the corresponding content.
Uncovered legacy paths invalidate totals. Import/restore staging integration is
described below. This is not complete coverage or aggregate publication.

When later approved and enabled, counting is O(changed saved text length), capped
at one million characters per block, outside transaction retry loops. It adds a
small operational count to each changed block, and to journal images of that
block; there is no extra per-block write. That new metadata is a privacy expansion
requiring activation approval even though no plaintext enters Admin.

Native/document import shared `_prepare_import_entity` and encrypted live restore
now use the same explicit domain option to recompute block contributions from
plaintext already authorized by those workflows. Incoming counts are not trusted.
Restore computes only after its existing readback check; it adds no decryption
operation for counting. Publication still does NOT initialize a project ledger,
so these contributions alone cannot produce a complete Admin total. Synthetic
regressions exercise rejection of malicious supplied counts and actual encrypted
restore without exposing content in operational metadata.

## Revision binding and explicit metadata initialization (still uncomposed)

Contributions now include `source_generation`. A count is usable only when its
schema/definition/count are valid and that generation equals its canonical block
revision. Disabled-writer changes leave stale metadata unusable. Idempotency
fingerprints exclude only this derived source generation, preserving count and
all original request semantics. Undo/Redo rebind only an already-valid historical
contribution after the existing exact-image CAS; their semantic chain comparison
ignores only the derived generation while retaining count/definition checks.

`SavedWordCoverageGuard.initialize_metadata` is a separate explicit opt-in method,
not called by runtime or Admin. It requires an active project's exact expected
revision, then scans at most 1001 direct block metadata records within that same
transaction. The Google adapter projects only root status/generation and block
generation/contribution. 1000 blocks is the completeness cap; the extra record is
an overflow sentinel. Missing, stale or malformed contributions and overflow
publish unknown, never a partial total. No text, body object or decryption is read.
This can establish complete zero only from a verified empty canonical collection.

The initializer's 30-second budget bounds/checks projected reads, NOT provider
transaction begin/commit latency. It requests one transaction attempt; any
uncertain commit must be checked by a future authorized caller, not retried
blindly. Maximum scan billing is 1001 projected block document reads plus the
project read and one ledger write per attempt (empty query has provider minimum
billing). Projection reduces exposed fields, not per-document read billing.
There is no scheduler, manual UI action, quota allocation, initializer endpoint or
activation configuration yet. Existing ledger initialization/backfill remains
unapproved and unperformed.

For an already verified complete ledger at the previous project revision, the
transaction guard can now apply exact before/after bound block contributions and
advance the aggregate atomically. Unknown never becomes complete from mutation.
Missing bindings, stale revisions or unsupported mutation paths invalidate it.
Root-only metadata changes may preserve counts when the canonical revision is
unchanged; ordinary generation advances preserve a complete total with zero block
delta. Explicit initialization remains necessary for legacy/missing coverage.
External direct writes that forge or bypass canonical revisions are outside this
proof and must not be presented as covered. Account-owner attribution and Admin
policy/exclusion aggregation are still separate, unimplemented production steps.
