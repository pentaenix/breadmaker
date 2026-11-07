# Resumable hosted project package imports

The `project_package_import_resumable` capability opts new clients into the native
package job protocol. Existing clients can continue the inline multipart endpoint.
This adds no revision-root storage format and does not activate unrelated imports.

- `POST /api/projects/import?resumable=true` takes the existing multipart file and
  optional `name` query, plus a stable `Idempotency-Key`.
- `GET /api/projects/import/jobs/{job_id}` reads authoritative progress.
- `POST .../{job_id}/step?batch=N` stages at most 64 canonical records with at most
  eight parallel uploads. One existing annotation pack is one canonical record;
  its established row/encoded/decoded limits still apply.
- `POST .../{job_id}/finish` publishes the project, owner, completion receipt and
  account selection in one transaction.
- `POST .../{job_id}/cancel` atomically prevents publication. If publication won,
  it returns `HOSTED_IMPORT_ALREADY_PUBLISHED`; deletion is a separate user choice.

Every route derives its actor from the authenticated server session and ignores
existing project selectors. Structural action headers are rejected. Blocking
parsing/provider work runs off the event loop with a two-request capacity limit;
no persistent in-process background job owns progress.

The response carries `job_id`, `phase`, `next_batch`, `total_batches`,
`completed_records`, `total_records`, `completed_images`, `total_images`,
`source_sha256` and `ready`. Phases are `preparing`, `staging`, `ready`, `completed`
and `cancelled`. Completed status and finish include the original `project` and
`reload: true`, including after a lost response or replica change.

Preparation validates the complete archive before content staging. A source hash,
actor, operation and whole-plan fingerprint pin the immutable source. Plans live
in bounded content-addressed objects; individual small descriptors live below the
account job, not in one growing Firestore document. Preparation commits after at
most eight plan uploads. Replaying the same file/key after interruption skips the
committed prefix. A `preparing` response therefore requires replaying begin with
the same file/key, not requesting batch zero.

Each batch verifies its exact path, generation, hash, decoded size and schema.
Fresh job/reservation/account/erasure checks fence object reads and writes. The
canonical metadata and chained cumulative receipt advance atomically; finish
requires the complete expected receipt. Cancellation and expiry cannot race a
late canonical write or project publication. Active jobs expire after 24 hours;
completed receipts remain replayable rather than expiring into a duplicate.

Canonical per-record objects and complete nested parent paths remain readable by
10.5.95. Optional read bundles reuse the existing acceleration schema and never
replace a paragraph's canonical object. Source bounds remain 64 MiB HTTP,
256 MiB expanded ZIP and 12 MiB per image, with existing image structure checks.

## Retention and account erasure

`users/{uid}/package_imports/{job_id}` and its exact `plans/{index}` and
`receipts/head` descendants have explicit ownership classification. A matching
actor-bound reservation and derived project ID are required; unknown descendants
remain completeness blockers. Unpublished project prefixes are inventoried even
when they contain only plan objects, or the job is cancelled/expired.

Account closure freezes writes and records retained import prefixes independently
of published project membership. The account-data rehearsal refuses to erase the
binding/receipts while staging remains. This feature performs no physical cleanup
and grants no purge authority: orphan-aware census, upload quiescence, retention,
provider version checks and the existing independent purge gates remain required.
