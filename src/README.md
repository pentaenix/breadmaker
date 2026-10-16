# Breadmaker source architecture — 10.3.68

Local mode keeps the verified SQLite/filesystem product stack and is accountless. Hosted mode uses the separate Firestore/GCS composition under `src/hosted` and Google Identity Platform as its sole real authentication authority.

`src/auth/identity_platform.py` and `identity_platform_security.py` own hosted authentication/security policy. `src/hosted/http_auth.py` exposes the server-cookie exchange and provider-neutral Account & Security routes. Browser Firebase code is transient proof only; Breadmaker authority is the server cookie plus Breadmaker project authorization.

The retired Auth0 implementation and checkpoint source notes are preserved under `docs/archive/auth0_runtime/` and are not importable runtime code. Local runtime, Schema 42 and the frozen hosted Firestore/GCS adapters remain separate boundaries.

M8A retry/concurrency documentation: `docs/HOSTED_CLOUD_RUN_LIFECYCLE_M8A.md`.
