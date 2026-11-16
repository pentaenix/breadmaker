# Account-owned product metrics

The owner explicitly requested account-owned counts, derived eligible-account
totals, and counting existing current manuscripts. No independently maintained
fleet numeric counter is part of this design. Canonical project records remain
content authority; the account's backend metric record is reporting authority.
Admin sums eligible account values only after completeness and ownership checks.

## Current audit and target ownership

| Metric family | Existing source | Account-owned target | Migration limits |
| --- | --- | --- | --- |
| Saved current manuscript words | Project contribution metadata and operational project proofs; reader sums projects | `users/{uid}/metrics/saved_words_v1`, with generation-bound owned-project manifest | Legacy plaintext must be counted through authorized App readers; no history/cuts/titles/unsaved text |
| Active accounts | Explicit active root user records, fixed COUNT query | Eligible active account contributes one | These are application accounts, not distinct humans or online users |
| Current projects, chapters, scenes, canvases, tasks, story maps, reviews | Active project roots plus six fixed child COUNTs | Owner-scoped structural snapshot and canonical project manifest | Extant records, including archived tasks; no lifetime/activity inference |
| Project modification recency | Root generation and server `updated_at` | Owner-scoped current project timestamp manifest; evaluate windows at observation time | Rename/member changes can count; this is not writing/engagement history |
| Current map lines | Existing map parents and line COUNTs | Owner-scoped map/line snapshot with parent and generation fences | Parent absence, unsupported roots and overflow remain unknown |
| Daily usage | Validation-only `users/{uid}/usage_daily/{date}` | Already account-scoped; independent authoritative collection still required | Do not invent historical active minutes, daily writing or pages from current text |
| Storage | Authorized project/user object prefixes and generations | Charge current owned objects once to owner; distinguish retained archives/versions | Shared object/asset deduplication and provider retention require explicit definitions |
| Infrastructure spend, service health, registry, requests and CPU | Provider billing/monitoring | Remain application/service metrics | Do not assign unattributed provider costs to accounts or call sums exact user costs |
| Payments/business costs | Their existing private provider/manual records | Remain separate unless a verified association is explicitly approved | No email/name joins or invented account attribution |

Each shared project is assigned once to its independently verified unique current
owner incarnation. Editors/participants do not receive a second copy. Unknown or
multiple owners prevent a complete total. Provider UID and hosted `usr_` IDs stay
distinct. Current account status and birth pin select eligible records: a deleted
or inactive account contributes nothing even while its retained metadata exists.
Account recreation never inherits an old metric record. A physical root deletion
does not cascade Firestore subcollections; erasure inventory must include these
owned metric/proof/job paths and honor existing retention. No deletion is run by
this implementation.

## Saved-word source and canonical maintenance

The account record contains its current numeric saved-word value plus a bounded
map of owned project incarnation/revision proofs. Each proof tracks known block
count and known words internally. An incomplete account publishes null, never a
partial numeric total or fabricated zero. A verified empty owned-project manifest
can publish zero. An independent current ownership/generation manifest must match
the record before an Admin sum is usable. No project words enter the sum through
an independent fleet numeric accumulator.

Under the existing explicit contribution flag, the canonical chain becomes
journal -> existing project proof guard -> account word guard -> lifecycle ->
provider. Account numeric state commits in the same transaction as content and
revision. Operational records stay outside Undo images. Read-bundle attachment
does not invalidate a count. A participant save changes the verified owner's
record. The account guard does not change project membership, authentication or
collaborator access. The existing lifecycle fence retains write authority.

The old project proof documents are preserved as compatibility/migration evidence;
they are not the numeric reporting source of the new account reader. Default-off
composition stays inert. Existing live feed extractors must recognize only the
exact reviewed new wrapper profile and source hashes before enabling it; generic
unwrap or relaxed package/source/scan checks are not acceptable.

## Historical current-manuscript backfill

Backfill writes derived state only under the owning account. It does not resave
canonical blocks, advance manuscript generations, change modification timestamps,
rewrite journal images or read historical versions. This avoids invalidating
strict Undo CAS comparisons with newly added operational fields. A separate
generation-bound proof under `users/{uid}/word_block_counts/{opaque}` supplies a
legacy block's count when its canonical record lacks contribution metadata.
Proof keys hash identifiers/incarnation pins, never plaintext.

The worker must first inspect a bounded complete owner-membership manifest and
pin account birth, unique current owner, project birth and generation. Per-project
jobs live under `users/{uid}/metric_jobs/` and page by block document name. A page
uses at most 33 projected rows (32 plus sentinel), 2,000,000 text characters and
8 MiB decoded text; an individual block retains the current one-million-character
count limit. Text exists only in worker memory. Cursor, sums and proof writes
commit atomically under the same pinned current root/owner/account state. Changed
content, account freeze, owner drift, budget overflow and uncertain commit cannot
publish a complete count or trigger a blind retry. Read the persisted job state
after uncertainty; repeated old cursors must never add a page twice.

The normal unbounded SDK transaction/content readers are not sufficient for a
production backfill claim. Explicit finite RPC budgets, no SDK retry, object
generation/hash/size checks, decode limits and caller authorization must be proved
by the production adapter. `BoundedAccountWordStore` uses explicit begin/commit,
no automatic retries, at most 512 reads/128 writes and two-second metadata RPCs.
`BoundedCurrentBlockReader` rechecks active profile, erasure fence, unique owner,
active project and current block generation in its transaction. It reads only
that current block and its canonical content-addressed object, with generation,
size and SHA checks, one range GET (four-second maximum), eight-MiB gzip decode
limit and bounded returned text. It ignores read-bundle caches. Unverified private
codecs fail closed; this does not enable or bypass encryption. Tests exercise the
pinned real Firestore SDK framing and synthetic GCS responses with sockets denied.
A synthetic rehearsal is not production access or a live backfill receipt. No
production backfill has run at this checkpoint. Execution still needs an operator
adapter, finite whole-run cost allowance and exact scope/coverage evidence; the
worker never loops an entire fleet automatically.

Restoring an old Undo image without a count can make coverage unknown. A bounded
current-state repair must count only the now-current authorized blocks, without
rewriting the historical image. That path must be tested before claiming complete
coverage across existing Undo history. New ordinary counted edits and Undo/Redo
already carry the exact generation-bound contribution metadata.

## Reporting, exclusions, cache and deletion

The new reader must enumerate active account incarnations, validate their owned
manifests, then sum those account records. Missing/stale records remain unavailable.
It must not silently fall back to the old project-sum producer or reuse a legacy
fleet cache under a new definition. Response observations are derived snapshots,
not an independently maintained general counter. Current eligibility must be
checked before returning a retained snapshot after account deletion/status change;
otherwise hide it until a permitted refresh. Existing shared 30/month, 900-second
refresh and whole-app USD10 budget remain unchanged.

`GoogleFirestoreAccountWordsProducer` implements the new, currently unselected
`cloud_account_saved_words_v1` source with schema version 2. It reads only account
numeric state, projects/owners and identity metadata. It rechecks active user and
project populations, erasure fences, owner queries, inactive witnesses and policy
revision. Missing/inactive/frozen account roots contribute zero without changing
retained metric documents. Retained exclusion entries for those inactive accounts
do not require a provider/account cleanup just to remove their contribution.
Active incarnation mismatch still fails closed. Partial/missing/stale account
coverage publishes null. Paired Cloud/Admin validation and cache eligibility
revalidation are still required before selecting this new source. The legacy
source remains untouched and must not be selected as a fallback.

The previous Test policy is scoped to saved words. Whether the same Test setting
excludes all product metric families is a pending clarification; account ownership
and deletion eligibility apply to every attributable family either way. No filter
scope is silently broadened. Infrastructure metrics stay separate.

The exclusion decision journal is control/audit data rather than a product count.
Its records need explicit owner/target incarnation attribution and retention review.
Moving numeric sources into user subtrees does not authorize deleting old policy,
receipts or proofs, or resetting quota. The previously proposed durable journal
must be reassessed against this account model before execution activation; no
128-cap workaround or automatic compaction is included.

## Account-scoped Test policy and bounded decision history

The recommended replacement for the shared 32-target array/128-decision lifetime
book is a backend-only Test setting and receipt collection under the target
account, bound to its account birth. Absence means ordinary account only after a
complete, checked migration of existing choices. Every operation must pin target
birth, setting revision and exact reviewed action. Current settings are read from
eligible accounts; a sorted fingerprint of their incarnation/setting revisions
can bind a response observation without a separately incremented global numeric
counter. Shared monthly admission/cooldown control remains application control,
so deleting an account must not refund attempts or reset a safety budget.

Keep minimal operation/review receipts with the target account; do not duplicate
target pins into an indefinite shared journal. While that account incarnation
exists, a reused key must return its existing fact or reject a changed request.
After deletion, an old request must reject because its pinned incarnation no
longer exists; a recreated ID must not inherit the old setting or receipt. This
allows source removal to remove contributions without a global-counter repair.
Receipt retention and later physical erasure follow the existing reviewed account
retention policy; no TTL, destructive compaction or purge is introduced here.
Admin should show a deleted target as unavailable, rather than promise indefinite
per-account history after physical erasure. If indefinite history is required,
that is a different retention guarantee needing an explicit decision.

This model has bounded per-operation work and bounded monthly admission, with
storage growth proportional to actual decisions rather than a full-copy journal
array. The existing conservative journal cost model remains an upper-bound
comparison, not an implemented quota or evidence of whole-app headroom. Actual
production billing is denominated separately and must not be called USD headroom
without a verified conversion/budget reconciliation. This account-scoped protocol
is a design recommendation, not yet the running policy executor. Migration must
read and preserve every current choice, prohibit simultaneous legacy/new mutation,
test replay and deletion, and publish the new Cloud/Admin protocol together.

## Concrete audit references and remaining migration

The current Editor collectors are `security/inventory.py` (two fixed status
COUNTs), `security/product_inventory.py` (root plus six structural COUNTs),
`security/project_recency.py` (root metadata time windows), and
`security/map_line_inventory.py` (map parents and line COUNTs). Their response
caches live in Cloud's shared operational control documents. Those are snapshots
of live source queries, not independently incremented lifetime counters; they can
still retain stale contributions after account closure until eligibility-aware
read validation replaces that behavior. This implementation does not claim those
four live feed families have already migrated to stored account snapshots.

`security/metrics.py` validates account-scoped daily usage only; no historical
activity collector is running through that module. `account_deletion.py` includes
all `users/{uid}` descendants in its inventory and keeps unfamiliar descendants
as explicit completeness blockers. Thus the new metric/proof/job paths cannot be
silently omitted, but they are not automatically classified or authorized for
erasure. Cloud's real-provider erasure planner separately retains shared legacy
policy/cache/ledger review scope. Orphan ledgers, shared caches, provider versions
and backups require their own inventory/retention handling. Moving the new source
does not silently delete any of that existing data.

## Migration sequence

1. Integrate compatible default-off project-proof prerequisites and preserve data.
2. Test account state, canonical maintenance, paged backfill and strict production
   transport without exposing manuscript content in Admin or artifacts.
3. Update exact Cloud wrapper/producer and Admin schema pins together; retain the
   existing feeds until each account-source replacement is verified and selected.
4. Build/scan exact images. Deploy code separately from explicitly enabling count
   maintenance, executing bounded backfill and activating reporting.
5. Verify current complete coverage and owner-selected Test behavior, including
   shared projects, edit/replay/Undo, deletion eligibility and interrupted jobs.

Source tests, a merge and deployment each prove different things. None alone is
a complete coverage, authenticated live-flow or physical-erasure claim.
