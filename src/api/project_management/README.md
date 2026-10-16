# Project Management ownership

`api.project_management` owns project/profile/workspace administration HTTP routes. The package boundary only composes routers; callers and tests target the canonical owner.

- `profiles.py` — profile CRUD, activation, avatars, public profile projection.
- `workspace.py` — workspace state and current identity.
- `metadata.py` — project statistics used by Project Center.
- `destructive_actions.py` — danger challenges, project deletion, Delete All Data.
- `projects.py` — create, activate, rename.
- `members.py` — membership administration and privacy-safe member projection.
- `packages.py` — native `.breadmaker` project import/export and whole local-data export.
- `json_transfer.py` — public Project/Fragment JSON preview/import/export.
- `assets.py` — project asset reads.
- `health.py` — deep project verification and storage accounting.

Do not recreate `backend/project_routes.py` or add compatibility re-exports to this package.
