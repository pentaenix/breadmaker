# Inactive manuscript revision image helpers

`hosted.revision_images` is a synthetic helper experiment, not an enabled import
path. Runtime activation and deployment remain separate. The existing journal
limits and private-project restrictions are unchanged.
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
3. Before legacy adoption, call `publish_sources(project_id, user_id, job,
   payloads)`. It revalidates the whole payload set, publishes canonical immutable
   image bytes, and stores complete pinned `asset_source_receipts` in a freshly
   authorized transaction. It creates **no asset metadata**, so even an unaware
   committed-asset reader cannot resolve new source URLs. Release the request's
   payload map once this succeeds; adoption can now take multiple HTTP requests.
   Publication uses at most eight worker threads, each with its own copied request
   context. The object store must be an `AccountLifecycleObjectStore` bound to the
   exact same lifecycle document fence as the revision reader.
4. After adoption, repeatedly call `stage_sources(project_id, user_id, job, index)`
   with the image offset returned as `asset_next_index`. Each call checks the
   transaction-local format marker, validates **every** source receipt against the
   project-scoped manifest before reading any source, and reads/inspects at most
   eight images using their exact pinned generations. A fresh authorized
   transaction atomically stages their hidden metadata, appends the matching
   `asset_receipts` prefix and advances `asset_next_index`. The initial cursor is
   zero with an empty receipt prefix. Omit `asset_source_receipts` until source
   publication succeeds; a present partial list is invalid. Steps return `asset_next_index`, `asset_total`
   and `assets_ready`; completed offsets replay read-only, while future or invalid
   offsets reject. The parent owns the images phase inside preparing/staging and
   must not advance manuscript-content batches until images are ready. Staging
   never advances the project's semantic generation. The original `stage` API is
   retained for inactive callers needing one atomic all-images metadata batch;
   it now shares validation, bounded parallel publication and complete receipts.
5. Immediately before final publication, call `verify_for_publication` to re-read
   the exact pinned object generations and verify their hashes and structure.
   Final verification requires the complete source and metadata receipt lists and
   the finished image cursor. Pass its proof to
   `publication_changes(tx, project_id, user_id, job, proof)` inside
   the caller's final retained-view structural journal transaction, before any
   transaction writes. That helper rechecks the durable job and receipts and
   returns `(path, after_image, False)` mutations. Apply these in the **same**
   transaction as tab pointers, ordering, generation advance and the import receipt.
   The proof is an internal binding, not a browser token or an authorization grant.
   The existing final content-ready and idempotency checks remain the caller's job.
6. New image metadata becomes globally available only by removing pending revision
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

## Integrated journal budget and exact path hook

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
non-restorable. The exact canonical asset-ID hook is implemented and exercised
by real source staging, publication, Undo and Redo tests. Atomic rejection tests
cover both the unchanged journal limits and oversized retained before-images.
The staged publisher checks the combined conservative budget before any writes.

Cancellation, interrupted objects, collisions, failed journal commits and Undo
never delete immutable bytes. Such bytes remain retained/orphan candidates for the
separately reviewed retention and physical erasure protocol. Full release,
production rollback, capacity, recovery, and erasure proofs remain incomplete.
