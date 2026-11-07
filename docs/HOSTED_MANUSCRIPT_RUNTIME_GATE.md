# Manuscript reader rollout and writer gate

The hosted runtime now composes the normal document view as
`ActionDocumentStore(ManuscriptDocumentStore(AccountLifecycleDocumentStore(raw)))`
when the lifecycle fence is present. Object writes use that same lifecycle fence.
The reader remains installed regardless of the writer capability. Unfenced
`hosted_dev` fixtures also use the compatible reader but cannot enable writers.
Local SQLite runtime behavior is unchanged.

`BREADMAKER_HOSTED_MANUSCRIPT_REVISIONS_ENABLED` is false by default. Unset, `false`,
and `0` disable the `hosted_manuscript_revisions` capability. Only the exact value
`true` enables it, and startup rejects that value without a lifecycle fence.
Other values are configuration errors. No deployment, flag activation, or project
adoption is performed by adding this code.

A reader-compatible release must reach every application instance and applicable
operator before any writer is enabled. Once a root is adopted, disabling the flag
is a writer rollback, not permission to roll back to an old, incompatible reader.
Keep the shared reader and operator codec in every rollback candidate. Physical
recovery and erasure inventories still require their unfiltered operator views.
Full retained-revision recovery is not yet supported: writers must remain disabled
until that separate safety gate is satisfied. Reader-first code can be reviewed
and released independently; passing the synthetic suite does not clear this gate.

## HTTP protocol

All routes use the authenticated actor and current project from the existing
request boundary. A supplied project, job, generation, or action token is a
selector/precondition, never an authorization grant. Services recheck membership,
account erasure and project generation. Private encrypted imports/adoption remain
fail-closed; these routes do not install an alternate plaintext reader.

With the capability enabled:

- `POST /api/import?resumable=true` starts a manuscript replacement, preserving
  explicit replacement confirmation.
- `POST /api/documents/import?resumable=true` starts an append, preserving the
  selected `after_document_id`.
- `GET /api/import/revisions/{job_id}` returns durable progress.
- `POST /api/import/revisions/{job_id}/step?batch=N` performs one bounded step.
- `POST /api/import/revisions/{job_id}/finish` publishes with the structural action
  bound by existing middleware. The finish result retains its original Undo token.
- `POST /api/import/revisions/{job_id}/cancel` cancels unpublished work. Publication
  that already won the race requires Undo instead.
- `POST /api/manuscript/adoptions` starts bounded validation with an idempotency key
  and optional JSON `generation` as a compare-and-swap precondition.
- `GET /api/manuscript/adoptions/{job_id}` returns adoption progress.
- `POST /api/manuscript/adoptions/{job_id}/step?batch=N` validates one page, using
  the returned `next_step`.
- `POST /api/manuscript/adoptions/{job_id}/finish` installs the equivalent format
  only after validation is complete. An already-adopted begin returns
  `ready: true`, `activated: true`, and no new job.

Complete first-use adoption before opening the chapter deletion Undo action.
Adoption itself does not delete prose. There is no adoption cancel endpoint: a
client cancellation stops further steps and suppresses Delete, including after a
racing finish. Enabled chapter Delete calls revision deletion, which requires
adoption and never starts an unbounded legacy scan inside the delete request.

With the capability disabled, revision/adoption endpoints return a conflict, and
legacy small transactional import/delete APIs keep their existing bounds. The
compatible view still supports adopted-root reads, trash/restore and structural
Undo. A small chapter can use the existing bounded delete/Undo path; a large one
fails safely at the legacy limit. Disabled writers do not silently invoke the
revision deletion protocol.

## Verification scope

`tests/test_v10596_revision_routes.py` covers default-disabled behavior, strict
configuration, same-fence composition, HTTP actor/project/action binding, bounded
adoption, staged publication/cancellation/Undo, private-mode rejection, and a
reader-compatible restart with writers disabled including trash/restore and small
chapter Delete/Undo. These are synthetic deterministic checks, not evidence of
production latency, complete operator rollout, or permission to enable the flag.
