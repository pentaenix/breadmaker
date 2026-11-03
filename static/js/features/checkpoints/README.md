# Checkpoints frontend ownership

`workspace.js` owns the Checkpoints browser workspace: list/filter UI, comparison split view, diff navigation, details/editing, creation, delete, preflight, and apply/restore actions.

Checkpoint server behavior remains under `src/api/checkpoints/` locally and `src/hosted/checkpoint_ui.py` in hosted mode. After creation, refresh the manuscript revision before allowing further edits so capture cannot leave the next save using a stale generation. Keep browser presentation and interaction here; do not move it back to `static/app.js`.
