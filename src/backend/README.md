# Backend platform boundary

`backend/` owns application-level concerns that are shared across feature domains but do not belong to persistence, workspace, auth, security, runtime, backup, or revision packages.

Current focused owners include:

- domain-owned API `contracts.py` modules — shared HTTP/Pydantic request contracts.
- `content.py` — content validation/sanitization primitives.
- `environment.py` / `configuration.py` — runtime roots and read-only configuration.
- `data_access.py` — small canonical SQLite row/reference helpers.
- `request_context.py` — actor/project binding, authorization, and hosted quotas.
- `portable_review_runtime.py` — standalone/temporary Portable Review process state.
- `recovery_runtime.py` — Recovery schedulers and backup-destination coordinator state.
- `history_runtime.py` — Remote History worker coordination and explicit History landmarks.
- `project_activation.py` — canonical project activation/switching.
- `local_history.py` — asynchronous canonical Current → local History publication, including registration of the storage canonical-commit observer.
- `lifecycle.py` — FastAPI lifespan and persistence-worker shutdown ordering.
- `http_boundary.py` — shell/static security headers, config routes, and HTTP error translation.
- `client_leases.py` — per-project browser editing leases.
- `danger_challenges.py` — destructive-action confirmation state.
- `request_pipeline.py` — request auth/project binding, hosted guards, and startup project boot.
- `runtime_routes.py` — shell/locales/runtime/session/health/test-auth/lease routes.
- `core.py` — thin FastAPI composition only; it does not re-export implementation helpers.

Project/profile/workspace administration HTTP routes live under `src/api/project_management/`; product routes also live under `src/api/`, and feature modules **must not import `backend.core`**. New lower-level logic should prefer an existing runtime/storage/revision/backup module or a cohesive focused backend module rather than growing `core.py` or creating a replacement service locator.

The intended direction is:

```text
app.py (composition)
├── backend/core.py (thin composition)
├── backend/<focused-owner>.py
└── api/<domain>.py
    └── focused backend / runtime / storage / revision_store / backups services
```

Cross-feature calls may use `backend.dependencies.feature_call` for named lazy function edges when necessary to avoid an import-time cycle. Cross-feature mutable state is forbidden.

HTTP request payload contracts are not owned here. The former `backend/contracts.py` registry was removed in 10.2.26; each API domain owns its payload models.
