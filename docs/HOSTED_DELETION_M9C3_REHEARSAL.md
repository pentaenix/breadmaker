# M9C3 — Account deletion lifecycle rehearsal, Editor 10.3.81

**Not deployed. Not enabled for hosted users. Model did not run the test suite.**

This release adds `src/hosted/account_sandbox.py` to exercise the complete
account-*data* cleanup choreography against **exact** MemoryDocumentStore and
MemoryObjectStore instances. There is no HTTP route, Cloud Run worker, live
Google SDK deletion, runtime feature flag, Identity Platform revocation, or
production account-erasure authorization. The separate M9C2B project purge
rehearsal is similarly memory-only.

## Lifecycle and authority separation

1. A user must be authenticated as the same account by a trusted actor accessor.
   Other users cannot arm or replay a closure.
2. `HostedAccountDeletionPlanner.preview` exhaustively inventories memory
   Firestore documents, author-owned projects, collaborative memberships,
   binding/import records, user-scoped objects, and independent recovery
   metadata, with optimistic revision rechecks. Unrecognized user references,
   unknown user-profile subcollections, and missing parent projects are
   **blockers**, not deletion candidates. Generic `owner_user_id` and
   `actor_user_id` pointers are also fail-closed, rather than invisible.
3. An account with an owned project CANNOT arm the account rehearsal. Every
   owned manuscript must first complete its independent project backup,
   30-day trash retention, bounded purge preparation, and memory-only project
   purge. A single account job is never allowed to bypass those safeguards.
4. The project purge's append-only preparation and completion receipts stay
   outside user data as narrowly recognized **retained audit evidence**.
   Changed/new retained receipts stop account closure. Independent recovery
   archives remain untouched even after their project root is erased.
5. The account rehearsal persists a bounded `account_sandbox_closures/{uid}`
   receipt. Its manifest contains exact document revisions and user-scoped
   content object generations+SHA-256. A different operation key cannot seize
   the same account receipt. Manifest size is bounded to 128 work units and
   128 KiB; exceeding that fails closed, rather than truncating inventory.
6. `step` removes at most one user-scoped memory object or one document.
   Objects use generation preconditions, 30s leases and a durable in-flight
   claim. A crash between object removal and receipt advancement can be
   reconciled across reconstructed executor instances. Documents and the
   cursor advance in one memory transaction. New references, changed user
   records, newly created objects, or changes to collaborating project roots
   stop the process. Other owners' projects and memberships are not erased.
7. The terminal state is `sandbox_identity_revocation_pending`. Account
   data was erased **in the fake provider only**. The Google/Firebase
   identity is NOT revoked, and the state explicitly reports that external
   identity revocation is still required. No simulated success is a live
   deletion authorization.

## External provider go-live blockers

Before production is implementable, a narrowly authorized *non-HTTP* worker
must prove exhaustive live orphan-aware Firestore traversal, write-fencing
for every producer of project/user data, sharded signed or sealed manifests,
Firestore and GCS conditional deletion, idempotent steps and audit retention,
privileged identity-provider disable/revocation, completed predelete backups,
retention/legal policies, and destructive trials on disposable real GCP
resources. Firestore and GCS do not share a general transaction; memory-only
fencing is **not** proof of a cloud-wide snapshot. Normal HTTP/editor runtime
must not receive deletion privileges or the recovery bucket's delete IAM.

The already-existing `HostedProductionPurgeGate` remains a non-authorizing
read-only evidence checklist even if a caller supplies every named proof.
Cloud 10.4.38 is unchanged. The local Schema 42 runtime and M3/M4 frozen
Firestore/GCS adapters are unchanged. M9B advanced frozen-history portability,
Cloud Scheduler/Run Job deployment, and live recovery drills remain separate
unfulfilled gates.

## Conformance written, not run

`tests/test_v10381.py` includes 19 memory-only cases: authorization, replay,
bounded progress, foreign project preservation, orphan/unknown records,
object generation drift, session-independent crash recovery, worker lease,
transaction retry, retained audit receipts, end-to-end simulated project
purge followed by simulated account closure, and the permanent live-provider
hard deny. Only the user runs the full Editor/Cloud verifier.
