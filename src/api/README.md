# Backend API layout

Breadmaker's HTTP layer is feature-owned. Every feature domain is a package under `src/api/<domain>/`; `src/app.py` and `src/backend/core.py` are composition-only and platform HTTP concerns live in focused `src/backend/*` owners.

## Dependency rules

- Feature modules **must not import `backend.core`**. Import the narrowest canonical owner instead.
- Stable backend concerns live under focused owners such as domain-owned `contracts.py` modules, `backend.content`, `backend.environment`, `backend.configuration`, `backend.data_access`, and `backend.request_context`.
- Stateful platform concerns live under `backend.portable_review_runtime`, `backend.recovery_runtime`, `backend.history_runtime`, and `backend.project_activation`. Reassignable state has exactly one canonical owner.
- Cross-feature function dependencies use `feature_call("owner", "symbol")` when an ordinary import would create an import-time cycle. This is a named lazy function edge, not namespace injection.
- Cross-feature mutable state is forbidden. If multiple domains need shared mutable/process behavior, move it downward into a cohesive backend/runtime/storage service.
- `_bridge.py`, `globals().update`, broad re-export facades, and replacement service locators are not part of the architecture.


Every feature domain is a package. Large domains split real subdomains across focused owners; small domains use the minimal `routes.py` + `contracts.py` shape. In every case, package `__init__.py` is router composition only and implementation callers target the owning submodule directly. `annotations`, `comments`, `find_replace`, and `icon_markers` are the minimal reference layout; larger packages such as `manuscript`, `reviews`, `canvas`, `maps`, `checkpoints`, `durability`, `import_export`, and `project_management` demonstrate subdomain decomposition. Package boundaries do not re-export implementation internals.

Use `dependency_manifest.json` for machine-readable dependency ownership and `CODEMAP.md` / `route_manifest.json` for route ownership.

## Platform HTTP domain

`project_management/` owns profile, workspace, project administration, members, package/JSON transfer, project assets, and project health HTTP routes. It is registered by `backend.core` before the request pipeline to preserve the platform composition order; its package boundary is composition-only and does not re-export implementation helpers.

- `workspace_tools/` is composition-only; references, bookmark, UI state, and document lifecycle have separate owners.

## Backend freeze

10.2.27 is the backend architecture freeze before frontend decomposition. The API root should remain composition-only: `__init__.py` plus `shared_contracts.py`. New HTTP behavior belongs inside the owning domain package; do not add flat feature modules or compatibility facades.
