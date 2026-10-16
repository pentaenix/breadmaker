# M9C1 — Hosted project deletion: owner-scoped purge planning (read-only)

**Editor 10.3.71 / Cloud 10.4.38. USER VERIFICATION PENDING.**

This checkpoint makes the deleted-project lifecycle explicit without introducing a
new destructive capability. The existing hosted project `DELETE` continues to
**tombstone only**, does not remove any Firestore documents or GCS objects,
and remains the only deletion HTTP action. Hosted account-wide Delete All Data
remains unavailable; the local accountless Project Settings deletion is unchanged.

## Read-only planner

`hosted.purge_planning.HostedPurgePlanner` consumes the existing project
Firestore store, live-content object store, and an independent recovery object
store. Its owner-only `preview(project_id, owner_user_id)`:

- refuses unknown, non-owner, live, and untimestamped projects;
- requires the `deleted` tombstone and enforces a **minimum 30-day** delay
  before *eligibility* (eligibility is not permission to physically purge);
- enumerates registered-model Firestore child documents including reachable
  nested children; does not traverse outside `projects/<exact project id>`;
- inventories GCS live-content objects only beneath the exact project's
  `projects/<id>/` prefix; unrelated project/user/import prefixes are excluded;
- enumerates `recovery/v1/projects/<id>/` separately as **retained backup**
  objects, *never* as purge targets;
- bounds inventory entry counts, requires independent live/recovery stores,
  detects concurrent changes to the root project document, and produces a
  deterministic diagnostic manifest fingerprint;
- exposes a sanitized owner-facing count preview, not raw object names or
  nested document paths.

**There is no method to delete objects, erase Firestore, or delete an account.**
`physical_purge_authorized` and `document_inventory_exhaustive` are always
false. This is intentional. The current `DocumentStore` protocol cannot
provide an exhaustive scan of missing-parent/orphaned Firestore descendant
paths, and a deterministic inventory hash does not turn a non-atomic
multi-system scan into a transactional purge token. No caller may treat this
preview as an authorization to execute deletion.

## M9C2 requirements before any physical deletion

1. A provider-neutral exhaustive recursive Firestore scanner/pager, including
   orphaned nested subcollections; fail if coverage cannot be established.
2. Durable owner-validated deletion receipts with exact generation scope,
   idempotent resume cursor and per-object generation preconditions.
3. A freshly verified *independent* immutable recovery archive and a
   documented user-readable grace-period recovery flow. Keep recovery
   archives out of ordinary runtime deletion IAM.
4. State revalidation on every resumed batch; project ownership, retention
   period, linked participants and currently referenced objects must not be
   inferred from an old preview. Do not delete any outside-project Firestore
   path or `users/`, `imports/`, or other projects' GCS prefixes.
5. A separate account-deletion orchestration that inventories every owned
   project plus user-scoped data, respects shared projects and retention, and
   requires fresh identity authorization. Account deletion remains disabled.

Source-only conformance in `tests/test_v10371.py` checks tombstone-owner
restrictions, nested known paths, strict GCS scoping, 30-day floor, independent
recovery preservation, concurrency-failure and fingerprint changes. These tests
are for the user's full release verifier; the assistant has not run them.

This checkpoint continues from the **unverified 10.3.68–10.3.70** portability
changes. Advanced frozen Review/Canvas/checkpoint codecs remain blocked in the
unchanged, fail-closed M9B2B conversion layer. There is no live deployment,
independent GCS bucket or scheduled Cloud Run Job yet. Cloud 10.4.38,
Schema 42, the local runtime, frozen M3/M4 drivers, and the release verifier
are not modified.
