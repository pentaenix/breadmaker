# Tasks ownership

`api.tasks` is a router-composition boundary only.

- `lifecycle.py` — Task list/create/update/delete.
- `milestones.py` — milestone reads/writes/reorder and archived-task write guard.
- `duplication.py` — Task duplication.

Do not recreate `api/tasks.py` or re-export implementation helpers from the package boundary.
