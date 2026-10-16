# Milestone 8B — Whole-operation replay, bounded work, lifecycle policy

Editor **10.3.65** / Cloud **10.4.38**. M8B is a verification candidate, not a live GCP deployment. M8A remains carried into this release; the user runs the combined verifier. Local Breadmaker, Schema 42 and provider-independent offline behavior are unaffected.

## Whole-request replay for manuscript chapter replacement

The whole-request replay guarantee for chapter replacement is implemented at the Firestore commit boundary.

`PUT /api/chapters/{chapter_id}/blocks` no longer iterates per-block create/update/delete requests. It is one canonical Firestore transaction. The server:

1. Binds the actor to the **server-verified** session and resolves the project from authoritative project membership. Browser-supplied identity is never trusted.
2. Validates every block ID, chapter association, optional expected generation, duplicate ID and request size. It computes a request fingerprint and stable IDs from the scoped `Idempotency-Key` (or assigns a fresh one for an unkeyed request).
3. Publishes any content bytes as **immutable GCS objects first**, without making them canonical. Interrupted uploads may leave only safe, unreferenced objects for M9 cleanup.
4. Runs a **single Firestore transaction** that checks project membership, chapter existence, previous blocks and any operation receipt **before performing writes**. It atomically creates/updates/deletes the entire chapter block set, increments the project generation **once**, and creates a durable receipt under the project. No instance-local lock or process memory owns the commit.
5. A matching retry on another instance returns the same committed block IDs without incrementing generation. A reused key with altered content fails with a conflict. A completed operation subsequently superseded by a different committed generation fails as `HOSTED_REPLAY_SUPERSEDED`, rather than silently overwriting newer content. Concurrent stale writers fail project generation CAS.

The mutation is bounded to 150 new/existing blocks, 8 MiB canonical JSON, at most 512 KiB per structured document and 2 MiB total staged structured metadata, to keep within a conservative Firestore transaction budget. More extensive manuscripts need a future segmented edit/direct job protocol. This endpoint returns an explicit validation error rather than making a partial commit.

A server response may be lost after Firestore commits; a browser retry must **reuse the same key**. Ordinary new user actions mint new keys. Without the original key, the server cannot infer that two similar edits are the same user intention.

## Consistent read-only snapshots

`/api/bootstrap`, `/api/book`, checkpoint snapshot capture and autocommit now observe the project's canonical generation before and after their composed reads. A concurrent write produces `HOSTED_SNAPSHOT_GENERATION_CHANGED` (409), never a seemingly coherent mixture from several generations. Checkpoint content remains immutable-object-first, pointer-last under its existing publisher.

## Other multi-step routes: explicit safety gate

Hosted scene-wide block replacement/removal, Canvas Populate, and Map/Line Populate still have no reviewed transactional hosted port. These endpoints now fail explicitly with `HOSTED_ATOMIC_BULK_OPERATION_UNAVAILABLE` (409). They do **not** perform partial best-effort creates. Their complete UI feature parity remains a separate product task, not an implied guarantee of M8B. Single-record Canvas/Map/node/edge CRUD and M8A Firestore-only annotation batch behavior retain their existing CAS/idempotency logic. The caller must not assume that the mere presence of an `Idempotency-Key` makes arbitrary legacy multi-step actions atomic.

Destructive project deletion uses a Firestore tombstone; full GCS/Firestore purge, cross-service recovery, and irreversible account deletion are deliberately **M9** requirements. Identity Platform provider-sensitive updates enforce fresh, same-UID proof and revocation through the current M7 security boundary; M8B does not introduce a process-local step-up store.

## Cloud Run shutdown, concurrency and request budgets

* **Stateless routing (no sticky session)**: Identity Platform cookie verification, Firestore projects/memberships and GCS content are externally authoritative. At-most-once semantics derive from Firestore receipts/CAS, not container affinity.
* **SIGTERM**: Cloud Run service shutdown normally sends SIGTERM and grants about ten seconds before SIGKILL. The hosted Uvicorn process already uses an 8-second graceful-shutdown budget. There is no shutdown-only durable manuscript flush, process-local project write lock, or non-reconstructible queued work. Uncommitted transactions vanish; committed transactions remain accessible on another instance.
* **Cloud Run request timeout**: exceeding the service timeout closes the connection with HTTP 504 while the container may still continue processing. Never treat a 504 as proof that a mutation was rolled back. Retry with the *same* idempotency key, or perform a status read. Synchronous requests have finite upload/body limits and transaction budgets; future long imports/exports require durable GCS/Cloud Run Jobs with an explicit operation-status API.
* **Concurrency and scale**: per-instance concurrency, request timeout, min instances 0 and finite max instances are cost/load controls rather than correctness prerequisites. M10 will specify them in actual Cloud Run OpenTofu; the old staging VM configuration must not be treated as deployed Cloud Run infrastructure.
* **Rate limiting caveat**: the existing `HostedRateLimiter` is *instance-local best-effort load shedding*, never an authentication, authorization, global anti-abuse, or replay authority. M10/M12 must supply an upstream distributed traffic-control policy before public availability.
* **Readiness**: startup can use only immutable image files and ephemeral `/tmp` scratch; it cannot require a persistent local `/data` disk. The rollout cannot be declared live-ready until M10/M12 tests exercise real identity, Firestore, GCS, restart, scale-to-zero, and rollback.

## Verification boundary

`tests/test_v10364.py` is a credential-free conformance suite for full-set replacement across reconstructed runtime instances, stale-writer CAS, a simulated Firestore transaction callback retry, replay after later writes, interrupted GCS staging and request bounds. These are designed to run through the **user-operated** full verifier. M8A's tests remain in place. No assistant-run pytest, Playwright, verifier, or cloud apply.

Relevant provider contracts: [Firestore transaction semantics](https://firebase.google.com/docs/firestore/manage-data/transactions), [Cloud Run runtime and SIGTERM](https://docs.cloud.google.com/run/docs/container-contract), [Cloud Run request timeout](https://docs.cloud.google.com/run/docs/configuring/request-timeout).
