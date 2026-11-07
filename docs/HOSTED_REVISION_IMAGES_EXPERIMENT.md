# Inactive manuscript revision image helpers

`hosted.revision_images` is a synthetic helper experiment, not an enabled import
path. It does not change runtime construction, routes, the structural journal's
allowlist or limits, the format reader, deployment, or private-project support.
All activation gates in `HOSTED_MANUSCRIPT_REVISIONS_PLAN.md` remain required.

## Protocol and integration contract

1. Collect embedded DOCX image bytes through the existing parser callback. Run
   `prepare_revision_images(project_id, images)` with each image's bytes,
   extension and destination `tab_ids`. This returns the manifest and a separate
   bounded payload map. Repeated content is deduplicated. The manifest must be
   reserved in the actor/project/source-generation-bound import job as
   `asset_manifest`, along with the existing destination IDs, operation and expiry.
   Include the full manifest in the caller's source/request fingerprint. A changed
   image or destination must not reuse an earlier request's identity.
2. Construct `RevisionImageStager(domain, authorize)` only inside the inactive
   revision service. The authorizer is the caller's existing transaction-local job
   loader (for example `ManuscriptRevisionImport._load`). It must recheck current
   membership, source generation, actor and owner erasure fences, expiry and job
   state. The helper independently requires the authenticated lifecycle actor,
   normal revision-aware domain, matching durable job, membership and generation.
   Neither a raw provider nor an unauthenticated background fallback is accepted.
3. `stage(project_id, user_id, job, payloads)` revalidates all supplied bytes against
   the durable manifest before writing any objects. Immutable objects are published
   and verified through the established publisher. A second authorized transaction
   merges only hidden asset references and stores `asset_receipts` in the job.
   Content paths, full digests, dimensions, extensions, size and generation are
   checked. Staging does not advance the project's semantic generation.
4. Immediately before final publication, call `verify_for_publication` to re-read
   the exact pinned object generations and verify their hashes and structure. Pass
   its proof to `publication_changes(tx, project_id, user_id, job, proof)` inside
   the caller's final retained-view structural journal transaction, before any
   transaction writes. That helper rechecks the durable job and receipts and
   returns `(path, after_image, False)` mutations. Apply these in the **same**
   transaction as tab pointers, ordering, generation advance and the import receipt.
   The proof is an internal binding, not a browser token or an authorization grant.
   The existing final content-ready and idempotency checks remain the caller's job.
5. New image metadata becomes globally available only by removing pending revision
   markers in that final transaction. Its exact before/after images belong in the
   journal. Undo restores the pending markers, while Redo removes them again.
   Previously global/legacy assets never gain markers or change metadata, preserving
   availability for other tabs, History and Reviews. Hidden shared assets merge
   bounded job/tab references. A scoped-visible asset is rejected: this protocol
   never publishes a row while leaving scoped markers, and silently reusing such
   an unsupported row could hide it when the old tab is replaced.

The helper supports at most **64 distinct images / 32 MiB aggregate**, matching
new-project import. Each image retains the existing stricter **12 MiB** hostile
image bound and PNG/JPEG/WebP structural validation without native decoding. Each
asset has at most 64 pending references. IDs are `asset_<full SHA256>` under the
canonical project asset collection, and bytes use `project_asset_key`; browser
paths remain `img_<24 hex>.<extension>`. Name-prefix and metadata collisions reject
rather than overwrite. No images are served by object-prefix discovery.

## Exact journal budget and hook still required

Final publication adds **zero to 64 asset records**: zero for existing globally
available assets, one per hidden asset promoted. The parent must reserve these
alongside every tab create/update/order mutation. Do not raise the existing
100-record / 450,000-byte journal limits. The caller's conservative 95-record /
300,000-byte product budget must include image metadata before **and** after
images, including pre-existing pending references. Sixty-four images leave at
most 31 tab changes under that conservative record budget. Check the actual
serialized total, not an average per-image estimate.

The only additional journal path eligibility is the exact four-component
`projects/<authorized project>/assets/<asset id>` product path. Asset descendants,
other projects, memberships, permissions, root authority and receipts remain
non-restorable. The isolated tests monkeypatch this proposed capability in one
synthetic journal rehearsal, explicitly without changing the production allowlist.
The parent owns the narrow production hook, its path tests and aggregate budgets.

Cancellation, interrupted objects, collisions, failed journal commits and Undo
never delete immutable bytes. Such bytes remain retained/orphan candidates for the
separately reviewed retention and physical erasure protocol. Full release,
production rollback, capacity, recovery, and erasure proofs remain incomplete.
