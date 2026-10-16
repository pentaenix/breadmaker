# M9 Deletion Bundle — Production gap assessment + account erasure preflight

## Scope

This change continues M9 after the user-verified sandbox preparation layer and carries the 10.3.78 scheduled-backup and restore-drill source. The two failing M9D restore drills came from passing `MemoryDocumentStore` to `HostedUserRepository` rather than wrapping it in `HostedStructuredRepository`. The isolated importer now uses the same repository construction as the hosted developer runtime.

**No production deletion is enabled.** The HTTP API, Google Firestore/GCS adapters, Cloud IaC, Schema 42, and verification harness are unchanged. The existing simulated purge executor still accepts only exact memory-provider types. There is no account deletion route.

### M9C2C — privileged production deletion gates

`src/hosted/purge_production_gate.py` enumerates independent, REQUIRED proof artifacts for actual production deletion: dedicated non-HTTP worker identity, constrained IAM, recovery bucket separation, exhaustive orphan-aware discovery, write-fencing enforced by *every* mutator, bounded sealed manifest shards, generation-conditioned deletion replay, valid restorable predelete archive, checked retention/ownership at each batch, external audit receipts, and real GCP crash/restore drills. Its assessment is **informational only**: it never grants deletion, including when caller-supplied claims enumerate all proof names.

These preconditions are **NOT proven** by local memory tests. M10 infrastructure implementation and M12 deployment verification are necessary. Do not interpret simulator success or an inventory fingerprint as a transactionally frozen live Firestore/GCS snapshot.

### M9C3 — owner/collaborator/account erasure mapping

`src/hosted/account_deletion.py` is a read-only, memory-only exhaustive scanner across user profile descendants, owner/editor project memberships, identity bindings, import receipts, unknown documents referencing the user, and user-scoped avatar objects. It reports independent recovery archives without deleting them, detects orphan memberships, fences document revision drift, requires a self-identity from an injected trusted provider and never exposes provider binding paths in public summaries.

Account erasure is NOT equivalent to project deletion. A writer's projects require independent archive, trash retention and independently authorized project purge; collaborations require safe membership detachment without deleting another owner's manuscript; external provider identities require privileged provider revocation. Unknown account references fail closed. The current memory-only scanner is a proof-of-shape, **not a Google production inventory**. Complete Google discovery, provider account revocation, durable multi-project jobs and retention of non-user audit evidence remain future deployment gates.

### Open items for milestone closure

1. Operator full Editor+Cloud verification of this ZIP; model never runs the harness.
2. M9 rich Review/Canvas/history local-v2 full fidelity remains blocked; hosted-v3 recovery archives remain complete.
3. Cloud Scheduler/Run Job and protected recovery-bucket policies not yet deployed.
4. Independent live GCP write fence, shards, worker IAM, reverse-access protection, purge/drill validation, and account identity lifecycle missing; production erasure remains disabled.

## Editor 10.3.81 — account-data closure rehearsal and firewall synchronization

See `HOSTED_DELETION_M9C3_REHEARSAL.md`. Memory-only account closure now has
a bounded durable cursor, per-object generation checks, crash replay, foreign
project protection and retained project-purge audit receipts. Account closure
stops at an explicit *external identity revocation pending* state; neither a
GCP purge worker nor account deletion is deployed. 10.3.80 user verification
confirmed 92/92 durable browser workflows but found three stale exact
`tests/conftest.py` SHA-256 firewall digests; those three expected hashes have
been synchronized to the deliberately approved 10.3.80 browser-startup
change without changing or weakening any runtime watchdog.
