# Native project package import UI

The `.breadmaker` file picker in `static/js/features/projects/lifecycle.js` uses
the existing Manuscript import modal, progress track, compact actions and error
styles. It displays the selected filename immediately and clears the hidden file
input so selecting that file again works. Upload/preparation is indeterminate;
record/image counts and the progress track come from durable server status.

Hosted runtimes opt in with `project_package_import_resumable`. Local SQLite and
older hosted runtimes retain `POST /api/projects/import`, with indeterminate
progress and no claim that an in-flight one-shot request can be cancelled.

## Job lifecycle

- Begin: `POST /api/projects/import?resumable=true`, multipart `file`, stable
  `Idempotency-Key` for this actor/content/name attempt.
- Status: `GET /api/projects/import/jobs/{job_id}`.
- Stage: `POST .../step?batch={next_batch}`. The UI yields between durable steps.
- Finish: `POST .../finish`, opening only the exact returned new project.
- Cancel: `POST .../cancel`, enabled only after the server supplies a job ID and
  disabled while publication is pending.

Session storage holds only the actor, SHA-256, signature, idempotency key, job
pointer, terminal project ID and recovery flags. It never contains package bytes
or manuscript content. Reselecting the same file after reload resumes the same
job. A `preparing` job replays the begin request with the same file and key.

A lost finish response triggers a status read. Uncertain status, generic 404s,
and `HOSTED_IMPORT_NOT_FOUND` preserve the original pointer and key. They never
silently upload a new project. Completed jobs retain their exact project ID.
Only authoritative `HOSTED_IMPORT_EXPIRED` exposes **Start again**, an explicit
new attempt; confirmed cancelled jobs can be imported again on a new selection.

Back, Close and Escape dismiss the dialog; they do not claim server cancellation.
Work stops at the next response boundary and the saved pointer remains resumable.
A changed modal, actor, project, view or navigation generation prevents a stale
success or error from replacing the current UI or opening another project.
Cancellation/publication races reconcile server status and offer **Open project**
if publication already completed instead of claiming cancellation.

Native package requests never inherit another project's active structural Undo
token. Import creates a new project rather than modifying the current document.

## Verification

`tests/test_v10596_native_package_import_browser.py` uses the actual runtime,
modal, localization and lifecycle code with deterministic synthetic HTTP jobs.
Its 16 explicit browser functions cover a 4.69 MB selection, repeated inputs and
buttons, count-driven progress, phone/narrow/desktop layouts, refresh/resume,
preparation replay, publication and cancellation response loss/races, stale
navigation and actor isolation, both legacy routes, expiry and action headers.

These focused tests complement backend archive/transaction tests. They do not
replace an integrated real-app/API package import smoke or the release gate.
Run with one Chromium worker and the unchanged 15-second per-item watchdog.
