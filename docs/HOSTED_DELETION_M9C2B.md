# M9C2B — Bounded restart-safe purge execution rehearsal (sandbox only)

**Editor 10.3.77 / Cloud 10.4.38 — corrective checkpoint; awaiting user full verification.**

M9C2A's read-only deletion preparation remains authoritative and unchanged. This
checkpoint adds `src/hosted/purge_sandbox.py`, which deliberately supports only
**exact** `MemoryDocumentStore`, `MemoryObjectStore` (both live and recovery), and
`MemoryProjectTreeDiscovery` instances. It has **no HTTP endpoint, Cloud Run job,
Google SDK wiring, capability environment variable, or production deletion path**.
It really deletes test data **only in injected in-memory stores**. Do not interpret
its successful fake-provider execution as a live deletion capability or as
permission to erase a writer's content.

## Protocol implemented for conformance

1. Operator first creates an independent hosted-v3 archive **before** tombstone.
   The real M9C2A receipt must then pass owner, 30-day retention, exhaustive
   discovery, generation, and byte-level SHA-256 archive verification.
2. `arm` binds this preparation to a bounded sandbox execution manifest:
   full scoped document paths + revision tokens, exact GCS object names +
   generations + hashes, pre-tombstone archive identity, and an idempotency
   hash. Manifest length is capped at **128 work units** and **128 KiB**.
   A different key cannot replace it; arming does not erase anything.
3. `step` executes **one** GCS delete or **one** Firestore document delete at a
   time; the final owner membership and root document are removed together in
   the *same memory transaction*. Documents delete deepest-first.
4. Object work claims a durable inflight cursor + 30-second worker lease
   *before* a generation-conditioned removal. A simulated crash after object
   deletion but before cursor advancement can be resumed by a new instance.
   Missing objects are accepted only for the durably in-flight target. A
   changed object generation, newly introduced scoped object, new orphan
   document, or changed document revision halts progression.
5. Firestore-like document removal and cursor advancement are transactional
   within the memory driver. The full remaining document set is rechecked
   under that driver's transaction lock, including unknown child paths.
6. The predelete recovery archive is reverified on every step and is never
   removed. Ordinary user identity, avatars, unrelated projects, upload
   prefixes, independent backups and the read-only preparation receipt are
   excluded from the purge manifest.
7. The sandbox result explicitly reports `physical_purge_authorized=False`
   because it is **not authorization for production**.

## Production blockers (M9C2C / M9D / M10 / M12)

- **No globally atomic Firestore + GCS snapshot** exists. A collection
  traversal and a GCS list can race. The memory-only protocol is not an
  implementation of cross-provider locking or the required live write fence.
- Google administrative `show_missing=True` traversal must be verified on
  a disposable, real GCP project, with orphaned parents and paginated results.
- A live executor will require narrow IAM, an explicitly disabled-by-default
  privileged Cloud Run Job, a separate kill switch, conditional Firestore
  update-time deletion, generation CAS for GCS, a versioned **sharded** manifest,
  lease fencing, retries/dead-letter observability, and an independently tested
  restore/readback drill. It must never be reachable through the normal editor.
- A recovery archive matching project generation is not by itself proof that
  every new/unknown orphaned entity is captured in that archive. Before *live*
  deletion, manifest-to-backup coverage and post-restore equivalence must be
  demonstrated, or those orphaned entities must cause a hard refusal.
- Retention/backup policies and lawful account deletion are still separate
  unresolved work. M9B rich Review/Canvas/checkpoint history is not fully
  portable; unsupported data must continue to fail closed.

## Verification

`tests/test_v10376.py` adds ten credential-free, test-only cases for arming,
separate-instance resume, isolation, lease/after-delete crash recovery, replaced
GCS generations, new orphan data, corrupt/missing archive, and bounded manifests.
The assistant did **not** run the tests or verifier. The owner runs the full
Editor + Cloud verifier on this ZIP. Cloud 10.4.38 and local Schema 42/runtime
are unchanged. **M9C2B is not production-purge readiness.**

## 10.3.77 corrective: exact object-preservation conformance

The 10.3.76 operator verification returned four failures in `test_v10376.py`
because its fixed object-count assertions ignored the generated immutable
`block-payloads/` object. The tests now capture the actual scoped object
metadata (name, generation, SHA-256) before arming or attempting a refused
step, then compare the complete metadata set afterward. This is more strict
than merely comparing object counts and includes all generated payloads.
**No purge implementation or live deletion capability changed.**

The same operator run saw 92 E2E setup errors from a single Playwright
`sync_playwright().start()` initialization exceeding the unchanged hard
15-second timeout. The current-browser suite passed 94/94; this is a separate
startup/host-load diagnostic, not a confirmed regression in E2E behaviors.
The watchdog, fixture, browser runtime, and verifier are unchanged.
