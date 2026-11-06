# Reviews frontend ownership

Reviews is package-owned in 10.3.5. The package preserves the existing classic-script runtime model while separating writer-side Review management from Reviewer Mode and configuration tools.

- `view.js` — Reviewer Mode typography and view-popover preferences.
- `reader.js` — Reviewer Mode rendering, selections, annotations, feedback autosave, portable `.breadreview` sessions, passphrase UI, and drag/drop opening.
- `workspace.js` — writer-side Review rail, ordering, scope, overview, feedback cards, and collection/review menus.
  Phone navigation uses the shared `core/collections.js` drawer; selecting a
  Review dismisses it without changing the saved desktop rail preference.
- `details.js` — Review create/details dialogs and lifecycle command entry points.
- `panels.js` — Questionnaires, Participants, Review Markers, and their side-panel workflows.
- `lifecycle.js` — round/state history presentation and Reviews DOM event wiring.

Do not recreate `features/reviews.js` as a facade. Cross-file calls intentionally use the shared classic-script global namespace; ownership is the file containing the implementation.

Hosted Participant mutations use the normal authorized/idempotent write path, not
the product Undo journal: participant/access/lifecycle records remain denied by
that journal. Successful hosted changes discard stale structural receipts while
preserving manuscript edit history; local Participant Undo remains unchanged.
Bootstrap and Review-list responses include the same authorized participant
projection so refresh/reopen does not hide successfully saved participants.
