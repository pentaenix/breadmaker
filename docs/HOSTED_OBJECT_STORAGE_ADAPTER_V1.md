# Breadmaker Hosted Cloud Storage Adapter — Milestone 4

**Editor release:** 10.3.52  
**Frozen data contract:** `HOSTED_DATA_MODEL_V1`  
**Live GCP resources:** none  
**Hosted application integration:** deferred to Milestone 5

## Boundary

Milestone 4 implements the server-only object-storage side of the frozen hosted data model without changing local Breadmaker. Local Schema 42, SQLite/filesystem persistence, accountless `./run`, the local `LocalAssetStore`, and the existing undeployed hosted composition remain unchanged. The new package is `src/hosted/storage`.

The normal release verifier uses `MemoryObjectStore`; it requires no Google credentials. `GoogleCloudObjectStore` is a thin SDK boundary in `google_driver.py` and is not instantiated by the product runtime until Milestone 5.

## Generation preconditions

Every object mutation has an explicit generation precondition.

- immutable/create-only objects use `if_generation_match=0`;
- replacement and deletion require the exact previously observed positive generation;
- exact-generation reads are used while verifying bytes before Firestore publication;
- stale generation writes/deletes fail rather than silently replacing a newer object.

The deterministic memory driver mirrors those semantics with monotonically increasing object generations.

## Server-generated names

Application callers do not supply authority-bearing object paths. `keys.py` constructs all canonical names from validated project/user IDs, opaque server IDs, SHA-256 digests, fixed type suffixes, and bounded numeric generations. User filenames, titles, email addresses, URLs, and arbitrary path fragments are never interpolated into object keys. The hybrid Firestore/object publisher additionally validates that the object name belongs to the exact canonical key family for that logical entity and that the SHA-256 embedded in a content-addressed key matches the bytes being published; a merely traversal-safe arbitrary string is not sufficient.

Implemented canonical families include:

```text
projects/<project_id>/assets/sha256/<sha256>.<png|jpg|webp>
projects/<project_id>/block-payloads/<block_id>/<sha256>.json.gz
projects/<project_id>/cuts/<cut_id>/<sha256>.json.gz
projects/<project_id>/revisions/<generation20>/<revision_id>.revision.json.gz
projects/<project_id>/checkpoints/<checkpoint_id>/<sha256>.checkpoint.json.gz
projects/<project_id>/checkpoint-assets/sha256/<sha256>
projects/<project_id>/reviews/<review_id>/snapshots/<snapshot_id>/<sha256>.snapshot.json.gz
users/<user_id>/avatars/<avatar_revision>.webp
projects/<project_id>/exports/<export_id>/export.<fixed-type>
imports/<user_id>/<upload_id>/<fixed-server-filename>
```

## Immutable publication

`ImmutableObjectPublisher` performs the durable object half of the save protocol:

1. construct a server-generated name;
2. stream/write with create-only generation match `0`;
3. on retry, accept an existing object only as a candidate for exact identity reuse;
4. read the exact observed generation back through the bounded object interface;
5. recompute SHA-256 and size from stored bytes;
6. return the verified name, generation, SHA-256 and size for Firestore publication.

A duplicate retry therefore converges only when the remote bytes are identical. A same-name/different-content object is an integrity failure, not a successful retry.

## Pointer-last Firestore publication

`HostedHybridObjectPublisher` owns the cross-store ordering rule. It validates IDs/key family and performs a fail-closed membership precheck before creating bytes, then creates and verifies the immutable object and performs the authoritative Firestore metadata/CAS operation last. Authorization is repeated inside the final Firestore transaction so membership revocation cannot race publication. It never deletes the object merely because the Firestore operation fails.

For hosted revisions the Firestore transaction:

1. re-authorizes project membership;
2. rejects a stale expected project generation;
3. creates immutable revision metadata containing object name, generation, SHA-256, size and publication time;
4. advances the project generation exactly once;
5. publishes `current_revision_id` and the verified current object pointer;
6. reports success only after that transaction commits.

A replay of the same already-published revision returns the existing matching revision metadata instead of incrementing generation again. Replay identity includes the operation ID, verified object identity, project generation, author identity, and caller revision metadata; reusing a revision ID with different metadata is a conflict.

The generic hybrid publisher is deliberately narrower than the frozen model. It handles immutable-create or ordinary entity-generation-CAS hybrid entities only. Hybrid entities whose authoritative mutation policy is `chapter_generation_cas` (for example manuscript blocks) are rejected before object creation and require the specialized Milestone-5 transaction that owns the parent Chapter generation. Milestone 4 does not fake that transaction through an entity-level CAS.

## Orphans

Blob success plus Firestore failure is explicitly safe. The object remains an unreferenced orphan candidate. `discover_orphans()` compares a bounded object prefix to a caller-supplied authoritative reference set. It does not delete anything. `delete_confirmed_orphan()` requires the exact observed object generation and is intentionally separate so later retention/reachability policy owns destructive cleanup.

No failed save path performs speculative rollback deletion.

## Hosted asset port

`HostedAssetStore` implements the Milestone-1 environment-neutral `AssetStore` byte contract without filesystem paths. Milestone 4 deliberately leaves route/runtime orchestration to Milestone 5: `put_blob()` durably creates/verifies the immutable asset object, while the later hosted request composition must publish the authoritative `project_asset` Firestore record through the hybrid publisher before treating the upload as committed. It:

- obtains the project ID from a server-owned binding/provider;
- structurally validates PNG/JPEG/WebP bytes with the existing decoder-free hosted image inspection boundary before publication;
- preserves the hosted hard image ceiling;
- derives the GCS name from SHA-256 plus the detected normalized extension;
- returns the existing `/api/assets/img_<digest-prefix>.<ext>` logical API identity plus optional object name/generation metadata;
- exact-generation reads and validates the bytes again before serving them;
- leaves failed-request cleanup to orphan/retention GC rather than deleting immutable cloud data speculatively.

The main Cloud Run process still does not decode images. No new image format or decoder is introduced.

## Streaming and disposable filesystem

The object contract is chunk-oriented for both upload and download and applies a hard `max_bytes` at the adapter boundary. The Google driver uses Cloud Storage blob streaming with generation preconditions. Derived exports/import staging can therefore be emitted/read through object storage without making a Cloud Run local file the authoritative or only copy.

Milestone 4 does not yet migrate the existing package/export HTTP routes; that wiring is Milestone 5.

## SDK isolation

`google-cloud-storage==3.16.0` is pinned only in `requirements-hosted.txt`. Local `requirements.txt` remains Cloud Storage/Firestore/Firebase-free. The Google SDK import exists only in `src/hosted/storage/google_driver.py`.

## Milestone-4 exit gate

The user runs the full Editor + Cloud verifier. Required release coverage includes:

- create-only generation match behavior;
- duplicate immutable retry;
- stale generation replace/delete rejection;
- exact-generation read verification;
- content hash mismatch rejection;
- revision object success plus Firestore CAS failure leaves an orphan only;
- successful Firestore revision pointer references an existing exact-generation object;
- server-generated path traversal resistance;
- image type/size boundary preservation;
- deterministic orphan discovery and generation-CAS cleanup;
- local runtime/storage semantics remain unchanged;
- no hosted product API composition yet.
