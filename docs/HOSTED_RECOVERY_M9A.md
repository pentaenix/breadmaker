# Milestone 9A — Independent, immutable recovery archive foundation

**Candidate:** Editor `10.3.66` / Cloud `10.4.38`  
**Status:** awaiting user-run full verifier; M8A/M8B corrective `10.3.65` remains an inherited, unconfirmed gate.

## Ownership and boundaries

- This is a **backend foundation**, not yet a running scheduled backup system, browser control, disaster-restore console, or deployed independent GCS bucket.
- `HostedRecoveryArchiveService` is explicit about requiring a **second ObjectStore instance** for recovery, and refuses the active-content ObjectStore instance. Deployment isolation must be established by the independently named GCS recovery bucket and least-privilege IAM in M10; separate Python objects pointing at the same physical bucket are **not** sufficient.
- A snapshot operation is owner-only. The server checks a project-owner membership via Firestore, exports a self-contained hosted package, then checks the project generation again. Any concurrent canonical mutation invalidates the export. A partial/failed Firestore entity query aborts rather than silently omitting data.
- The archive is published under `recovery/v1/projects/{project_id}/{snapshot_id}.breadmaker`, with GCS `if_generation_match=0` and an exact-generation SHA-256 readback. The object name and metadata include enough information for recovery operators with bucket access to find the archive **without active Firestore**.
- Replay of the same owner/project/operation ID returns an independently checked earlier archive. The resulting archive is bounded at **64 MiB per synchronous request**; the existing package exporter can temporarily assemble as much as its own 256 MiB input cap before this recovery-specific limit rejects it. Larger projects require a bounded job/streaming protocol before production; the synchronous code never falls back to an unbounded copy.
- The public method `read` additionally requires live owner authority. For a *total* Firestore outage, a restricted operations identity, not an unauthenticated web request, must enumerate and retrieve from the recovery bucket. Account/ownership reconciliation follows M9B/M9C runbooks.
- A backup is an independent **copy**, not an independently validated restoration. M9B must prove hosted restore and **local Schema 42** restore. Existing hosted-v3 `.breadmaker` files are **not** accepted by the local v2 package importer.

## Cheapest defendable private-alpha recovery floor

Proposed M10/M9D desired state (not provisioned in M9A):

1. Separate region-matched GCS bucket for immutable recovery packages; public access prevention, uniform bucket-level access, no public bindings; Cloud Run runtime allowed create/get as needed for verify, plus a narrowly scoped object-metadata patch permission **if the existing Google adapter requires it**; **no delete, object overwrite, lifecycle, retention-policy or IAM administration**. M10 must validate the concrete IAM role set against the immutable uploader rather than assuming predefined roles suffice. Recovery operator identity is separate, audited, able to list/restore.
2. Source-controlled soft-delete/retention/versioning policy sufficient to protect packages from both an accidental runtime deletion and a mistaken account operation. **Retention at least 30 days** proposed for the initial alpha; final provider cost/feature selection belongs to M10 and must not be presumed deployed.
3. Low-cost daily scheduled job in M9D/M10; no in-process timer and no minimum Cloud Run instance required. Persist positive backup-success and failures as audit/monitoring signals. Do not call a project "backed up" if the source generation changed or the content-readback checksum failed.
4. Separate Firestore metadata export/recovery strategy is still required. A project archive captures canonical product entities/assets, **not** provider credentials, Identity Platform state, collaboration/membership authority, account profile state or all service operation receipts. A full account-wide Firestore disaster requires an operator restoration/reconciliation workflow; this M9A service alone does not establish whole-service recovery.
5. **No recovery archive may be deleted by a normal web app request.** For intentional irreversible erasure, M9C will define an authorized, audited purge operator and explicit handling of retention/legal deletion conflicts. M9A service exposes no delete API.

## Invariants for subsequent checkpoints

- No silent partial exports or self-certified checksums.
- No in-band restoration granting the importing user prior owner/member authority.
- Never authorize by knowing the archive object name, project ID, display name or email.
- Do not turn local SQLite/Schema 42 into a cloud client.
- No saved credentials, TOTP seeds, provider tokens or API keys inside recovery archives.
- Verified-only snapshots: current generation must not change during enumeration, and reads of hybrid/asset objects must match referenced immutable generation/digest.
- Cloud backup capability must fail closed if the independent recovery bucket is absent in M10.

## Deferred to M9B–D

**M9B:** freeze portable semantics and actual local-import translation, hosted restore into new project, semantic content-equivalence tests, user-facing export/restore policy.  
**M9C:** project trash deadlines, resumable idempotent purge, ownership-scoped object reachability and account-wide deletion.  
**M9D:** scheduled job/retention configuration, independent metadata recovery, recovery drills/alerts with M10-owned deployed infrastructure. M10 remains the owner of the deployable GCP resources.

## Gate

Source-only, credential-free conformance checks are authored in `tests/test_v10366.py` for restart/replay, separate store, owner scope, generation fence, corruption, bounds and fail-closed enumeration. Model does **not** run those or any Breadmaker verifier. User runs the combined Editor + Cloud verifier. M8A/M8B are not formally green until that combined user gate passes.
