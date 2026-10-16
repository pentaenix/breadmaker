# Milestone 8A — Hosted lifecycle and retry boundary

Implemented in Editor 10.3.62; carried unchanged in Editor 10.3.63, Cloud 10.4.38. **Awaiting user verification**. No live GCP resources deployed.

## Authority and instance lifetime

A hosted instance is disposable. Firestore owns project/child/membership/profile generations and canonical writes. Immutable content bytes are published to Cloud Storage under generation preconditions; metadata pointers commit last. Provider-signed Identity Platform cookies are independently verified by each instance. No client must stay on one instance, and a process-local lock, session table, or idle worker must not decide whether a write is committed. This milestone eliminates the obsolete `HOSTED_SINGLE_WORKER_REQUIRED` guard. A single Uvicorn worker remains an economical *default per container*, not a correctness rule. The multi-replica runtime uses the existing Firestore transaction and GCS precondition implementations; it does not change frozen M3/M4 drivers.

`hosted_dev` may receive the **same** `MemoryDocumentStore` / `MemoryObjectStore` injected into independently created runtime objects for deterministic restart and concurrency tests. Real hosted mode explicitly rejects adapter injection. `./run` keeps SQLite/Schema 42 with zero Firebase/GCP dependency.

Cloud Run serves a request with allocated CPU, but an instance can disappear during/after the response. Uvicorn handles SIGTERM and has a bounded 8-second graceful shutdown default in the hosted image. Hosted FastAPI lifespan deliberately owns no persistent worker, background scheduler or shutdown-only flush. An in-flight Firestore transaction either commits durably or does not; the client may query/retry as appropriate. Neither exit handlers nor ephemeral files are manuscript recovery mechanisms.

Production hosted Docker has no `/data` `VOLUME` and does not set `BREADMAKER_HOME`; only reconstructible scratch work may use `TMPDIR=/tmp/breadmaker`. This directory is disposable and is memory-backed on Cloud Run; do not use it as authoritative storage. Local and hosted-dev image paths are not changed.

## Request-body and inline-operation budgets

Outer ASGI counts chunked request bytes before FastAPI multipart parsing. The hosted routes also enforce bounded streaming reads for avatar, asset and inline `.breadmaker` package uploads even when invoked through a route test. The initial inline package cap is 64 MiB compressed; package members are preflight-bounded to 64 MiB individually (12 MiB per asset) and 256 MiB total expanded data, with at most 25,000 members. Larger exports/imports need a direct GCS upload or Cloud Run job design in M9/M10; they must not rely on long-lived background threads or unlimited in-memory buffering.

## Durable idempotency contract

The browser adds a fresh `Idempotency-Key` header for hosted mutations. An explicit caller retry may reuse the original nonce (`options.idempotencyKey`); a new user action gets a new nonce. Keys are short printable ASCII and are scoped to the **server-bound user, HTTP method, path and query**. Duplicate or malformed headers fail closed.

For supported canonical creates, the request key deterministically derives an opaque resource ID and operation ID. The existing Firestore transaction verifies the resource fingerprint and prevents a duplicate generation advance across replicas. Changing a payload under the same key yields a conflict. Project creation repeats stable project/seed identities. Firestore-only annotation batches recognize full atomic replay without publishing the first subset twice.

Portable `.breadmaker` import also records a durable operation receipt **before** staging any objects, bound to the user, package SHA-256 and requested title. A retry with the same bytes reuses the same project ID and staged child fingerprints; a changed package under the key conflicts. The receipt persists in Firestore and has no in-memory lock; M9 will define safe orphan/receipt retention and cleanup.

**M8B scope deliberately remains:** End-to-end audit of multi-step UI routes such as chapter block replacement, bulk canvas/map population, provider-sensitive operations and destructive flows. A generic `Idempotency-Key` on an *unlisted* multi-step route is **not** a promise of atomic whole-request replay. M8B must either make those operations transactional/durably resumable or return an explicit non-retryable boundary. Cloud Run concurrency/request-timeout/min/max instance cost controls are finalized in M10 IaC; they are not a substitute for data correctness.

## Verification

The user runs the complete Editor and Cloud release verifier. New `test_v10362.py` cases reconstruct runtime objects on shared fake Firestore/GCS storage, prove generation CAS and retry guards, and test package receipt conflicts. No test runner or live GCP apply is run by the assistant.

Source: [Cloud Run container runtime contract](https://docs.cloud.google.com/run/docs/container-contract) and [Cloud Run concurrency](https://docs.cloud.google.com/run/docs/about-concurrency).
