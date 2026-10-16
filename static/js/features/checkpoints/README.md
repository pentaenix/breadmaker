# Checkpoints frontend ownership

`workspace.js` owns the Checkpoints browser workspace: list/filter UI, comparison split view, diff navigation, details/editing, creation, delete, preflight, and apply/restore actions.

Checkpoint server behavior remains under `src/api/checkpoints/`. Keep browser presentation and interaction here; do not move it back to `static/app.js`.
