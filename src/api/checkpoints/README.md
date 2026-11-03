# Checkpoints ownership

`api.checkpoints` is a composition boundary only.

- `capture.py` — snapshot capture, encoding/decoding, durable insertion.
- `lineage.py` — Checkpoint row lookup, Chapter identity/lineage, public metadata.
- `comparison.py` — local read-only comparison routes and current-version projection.
- `formats.checkpoint_comparison` — pure escaped comparison rendering shared by local and hosted routes.
- `hosted.checkpoint_ui` — authorized hosted metadata projection, filtering and bounded read-only comparison; stored snapshots remain immutable and restore/apply keeps its separate protocol guard.
- `restore_support.py` — annotation/comment/Marker/Scene restoration support.
- `restore.py` — destructive Chapter/Book manuscript restoration.
- `lifecycle.py` — list/create/read/update/delete HTTP routes.
- `apply.py` — preflight, conflict reconciliation, safety History commit, apply routes.

Callers target canonical owners directly. Do not recreate `api/checkpoints.py` or re-export implementation helpers from the package boundary.
