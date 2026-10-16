# Workspace project ownership

- `recovery.py` — orphan/recovery discovery and initial Recovery state.
- `registry.py` — project registry, creation, rename, activation, paths.
- `packages.py` — `.breadmaker` export/verify/inspect/import and whole-data export.
- `trash.py` — project deletion, trash inventory, restoration.

`workspace.projects` composes these service responsibilities; the old flat `workspace/projects.py` must not return.
