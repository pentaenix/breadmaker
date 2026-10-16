# M9 operator integration — 10.3.82 candidate

**Status: candidate, not yet verified by the user; no GCP destructive permissions enabled.**

The combined Editor 10.3.81/Cloud 10.4.38 release passed 1,516 Editor tests (one known skip) and all 85 Cloud checks in the user's verifier. That verifies the *isolated* memory-provider deletion rehearsals and hosted-v3 recovery code. It does **not** certify Google IAM, backup bucket independence or runtime fencing in a deployed environment.

## Source delivered for 10.3.82

| Module | Responsibility | Allowed side effects |
| --- | --- | --- |
| `hosted/operator_project_preflight.py` | Google-only, owner/retention checked, orphan-aware, twice-scanned project inventory and full exact-byte pre-delete independent archive verification. Exposes aggregate diagnostics and an evidence fingerprint. | Read Firestore and live/recovery GCS only. **Never erases.** |
| `hosted/operator_recovery_plan.py` | Bounded inventory of recovery schedule rows; validates owner/root membership, active project, due timestamps, retention floors, generation and revisions; supports replay checks. | Read Firestore only. **No clock loop, no `tick` invocation.** |
| `hosted/operator_recovery_drills.py` | Explicit bounded batch for hosted-v3 offline restore drills using independent GCS archives and temporary in-memory providers. No live Firestore source is necessary. | Read independent GCS; mutate **isolated in-memory stores** only. |
| `hosted/operator_identity_review.py` | Check exact hash-key linkage of known Identity Platform issuer/subject pairs to Breadmaker user IDs, with a revision-bound digest. | Read known Firestore identity bindings only. **Not proof of provider revocation.** |
| `tests/test_v10382.py` | Source-only regression contracts for invariants, drift refusal, budgets, privacy and disabled deletion. | No tests run by the assistant. |

## Safety gates not met in a local test suite

1. **Google Firestore administrative orphan discovery.** Check real SDK/RPC `show_missing=True` behavior against intentionally deleted parents that still contain descendants. An unavailable API must cause a hard failure. Prove complete paging with 25k-entry cap rather than missing or truncating descendants.
2. **Provider write fence.** Every *normal* writer, importer, asynchronous request, and content publisher must reject writes across both Firestore and GCS once the project has entered an immutable purge epoch. All active workloads must acknowledge the fence. Two matching inventory snapshots do **not** prove a fence.
3. **Dedicated, non-HTTP privileged principal.** End-user request tokens must never obtain storage deletion permission. Delete-worker service account may delete only tombstoned live project data, not retained recovery objects, identity data or other tenants' documents. Verify IAM with live negative tests.
4. **Recovery bucket isolation.** Separate GCS bucket and encryption/IAM/retention policy. Assert the delete worker has no permission to delete archive object versions or modify retention/holds. Snapshots must be independently enumerable following full source Firestore loss.
5. **Bounded manifest and resumability.** Manifest must be sealed outside the deleted subtree; document revisions and GCS generations are pinned; per-step receipts are transactional or reconciled across crashes; lease fencing survives worker restarts. Mock memory transactions are not sufficient proof of Cloud Storage/Firestore semantics.
6. **Final mutation revalidation.** Before each real delete batch, re-check actor authority, legal retention, child collection completeness, object generations, archived restore bytes, purge epoch, and external audit append availability. Fail closed if any provider is unavailable.
7. **Live recovery drills.** On isolated staging projects: create manuscript, Reviews, Maps/Canvas, object attachments, versions; capture independent archive; destroy *only the disposable staging source*; restore to another isolated project and compare content. Prove recovery even when source Firestore is inaccessible.
8. **Account closure and identity.** Enumerate all global references, user-scoped objects and collaborator relations, including unknown/orphan paths. Complete owned-project deletion first; detach collaboration without touching other owners. Obtain authenticated Identity Platform Admin SDK revocation/deletion evidence for *each* external provider UID; do not infer provider deletion from absent Firestore documents.
9. **Append-only operations log.** Immutable operator receipts, retention evidence and revocation records must be stored outside deleted project/user paths with access controls and redacted PII; test crash replay and containment.
10. **Deployment of scheduled jobs.** Cloud Scheduler or equivalent triggers dedicated Cloud Run Jobs using private invoker IAM and auditable work identities. The current `HostedScheduledRecoveryService.tick` is implemented and tested in isolation but isn't wired as a deployed job.

`HostedProductionPurgeGate.assess()` remains a diagnostic-only checklist. Supplying every string in `REQUIRED_PROOFS` can never switch on deletion. No production erasure method or HTTP deletion route is added in 10.3.82.

## Current portability exception

Hosted-v3 archives preserve the hosted data and pass independent isolated restoration. Exporting **all** frozen advanced historical Review/Canvas/checkpoint structures to **local SQLite Schema 42** is not implemented; the bridge intentionally rejects unsupported cases rather than silently flattening or losing data. This is a format/feature design decision to resolve before claiming complete hosted-to-local portability.

## M9 exit recommendation

1. Require 10.3.82 user-owned combined Editor + Cloud verification first.
2. Mark M9 **operator boundary implementation candidate**, not fully *operationally proven*.
3. Use M10 to deploy isolated buckets, dedicated job identities, private invokers, Firestore/GCS write fences and staging drills. Do not enable live deletion as an implicit consequence of deployment.
4. Resolve the advanced local-history portability format explicitly and perform a final end-to-end lossless export/import audit before treating hosted-v3-to-local export as universal.
5. A follow-on separate security review and explicit human approval should be required before any worker capable of production deletion is built or granted permissions.

### Non-goals of this checkpoint

No Cloud apply, no production worker deployment, no broad service account privilege changes, no account identity-provider mutation, no Firestore/GCS physical deletion, no retention pruning, no automatic fallback that can lose manuscript content.
