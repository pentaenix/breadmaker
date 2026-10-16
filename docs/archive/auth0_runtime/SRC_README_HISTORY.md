# Backend/runtime boundary — Checkpoint 12
Runtime routes expose real authentication only under an authenticated hosted composition: `hosted_dev + auth0 + development` for the localhost sandbox or `hosted + auth0 + staging` for the single-instance staging deployment. Production is rejected. The pre-integration auth preview route remains deleted; direct project shell routes authorize membership before returning the application shell. Local runtime never instantiates hosted auth or secret loading.


## 10.3.27 — Checkpoint 10.6 corrective verification

Schema 42 identity remains current-only. Historical verification fixtures now bind current project IDs explicitly, and the development-only auth visual preview is separate from real Auth0 routes.

## 10.3.26 — Checkpoint 10.6: stable project identity and hosted project URLs

Schema 42 intentionally breaks every earlier pre-alpha project. Each current project now stores one immutable 32-hex `project_uid` inside SQLite; it must exactly match the portable manifest `project_id` and workspace folder/registry id. There is no migration or compatibility path. Hosted project URLs use `/p/:projectId` (plus `/canvas`, `/maps`, `/tasks`, `/reviews` suffixes), direct links resume the requested authorized project, and the hosted root resumes the account's last-opened project. An account with zero projects receives an in-memory blank manuscript; the first meaningful write creates the project, assigns its ID, persists the draft, and replaces the URL. The existing All Projects modal remains the only project browser. Local Breadmaker keeps its open-and-write behavior and does not gain authentication.

### 10.3.25 hosted authentication/security UI

`static/js/features/account/security.js` owns the hosted Account & Security surface. Runtime routes now expose same-origin JSON prepare endpoints so the browser can navigate top-level to Auth0 without fetching credentials cross-origin. Local runtime composition is unchanged.

### 10.3.24 hosted account security

Checkpoint 10 adds the hosted-only security service and route contracts. Fresh Auth0 reauthentication gates sensitive account/MFA/password/session operations; Checkpoint 10.5 will add the UI. Local runtime composition remains unchanged and accountless.

### 10.3.23 hosted account linking

`auth/linking.py` owns Checkpoint 9 dual-proof account linking. `backend/runtime_routes.py` exposes its start/callback endpoints only inside the existing `hosted_dev + auth0` composition. Normal local runtime remains accountless and cannot instantiate this path. Linking UI is intentionally absent until Checkpoint 10.5.

### 10.3.22 hosted authentication composition

`auth/database.py` owns verified Auth0 database identity mapping and `auth/hosted_login.py` owns the development-only Universal Login handoff. `backend/runtime_routes.py` conditionally exposes `/auth/login` and `/auth/callback` only when the selected hosted session mode is `auth0`; ordinary local composition cannot instantiate that path.

### 10.3.21 Apple identity ownership

`auth/apple.py` owns Checkpoint 6 Apple-specific identity mapping. It accepts only identities already verified by `auth/oidc.py` through `auth/auth0.py`, constrains them to Auth0's `apple|` subject strategy, delegates persistence to `auth/accounts.py` using the full issuer+subject key, recognizes both supported Sign in with Apple relay-domain generations, and preserves previously stored profile metadata when later Apple authorization omits first-login-only values. It owns no Apple signing key, provider token, HTTP route, or account-linking policy.

### 10.3.20 Google identity ownership

`auth/google.py` owns Checkpoint 5 Google-specific identity mapping. It accepts only identities already verified by `auth/oidc.py` through the `auth/auth0.py` boundary, constrains them to Auth0's `google-oauth2|` subject strategy, and delegates persistence to `auth/accounts.py` using the full issuer+subject identity key. It must not own Google API tokens, Drive authorization, HTTP routes, or email-based account linking.

### 10.3.19 Auth0 boundary ownership

`auth/auth0.py` owns Checkpoint 4 Auth0-specific application configuration and delegates protocol behavior to `auth/oidc.py`. It is dormant until a later checkpoint adds hosted login routes. Local runtime remains null-session/null-account and never instantiates this owner.

### 10.3.18 marker-hover corrective

No backend ownership or API behavior changes in 10.3.18; this release is a frontend hover reliability correction only.

### 10.3.17 corrective contract synchronization

No backend storage/schema/API feature is introduced. The release updates route verification for FastAPI's live included-router model and preserves the existing hosted security boundaries.

### 10.3.16 verification-only correction

No application source ownership or runtime capability changes in 10.3.16; the patch hardens release-tool interpreter selection around the existing 10.3.15 security baseline.

### 10.3.15 hostile-image boundary

`security/image_security.py` owns non-decoding hosted image validation. API upload/import/read surfaces call it before hosted publication or serving. Server-side project-image decoding remains disabled in hosted mode; local behavior remains local-only.

### 10.3.14 security note

`security/http_request.py` owns canonical ASGI request-path resolution for trust decisions. The dormant generic OIDC engine remains unchanged and unrouted; local mode remains account-free.

# Breadmaker backend source map

`src/` is intentionally an entrypoint-only root. Python implementation belongs to an owning package.

| Package | Owns |
| --- | --- |
| `api/` | FastAPI feature routes and feature-local orchestration |
| `backend/` | Application lifecycle, middleware, platform coordination, diagnostics |
| `auth/` | Local/hosted identity boundary, hosted external-identity registry, and opaque-session primitives |
| `security/` | Imported-content trust boundaries and hosted abuse/security guardrails |
| `formats/` | `.breadmaker`, fragment JSON, and `.breadreview` serialization |
| `storage/` | Canonical project persistence split across context/database/mutations/History/Undo/assets/integrity/Recovery owners; see `storage/README.md` |
| `workspace/` | Installation/project repository orchestration; see `workspace/README.md` for explicit profile/project/backup/history owners |
| `runtime/` | Local and hosted environment adapters/contracts |
| `backups/` | Frozen snapshots, publication, retention, destination providers/scheduling |
| `revision_store/` | Immutable History/revision storage, restore, remote history, GC |
| `reliability/` | Deterministic reliability support |

Root Python files are limited to:

- `app.py` — application composition only.
- `server.py` — outer ASGI/server boundary.
- `__init__.py` — package marker.

Do not add a new loose `src/foo.py`. Choose the owning package first. If no package owns the concept, that is an architecture decision and should be explicit rather than solved with a generic `utils.py`/`helpers.py` file.

For HTTP navigation start with `api/CODEMAP.md`; for exact symbols use `../docs/CODE_INDEX.json`. See `../AGENTS.md` for dependency and invariant rules.

## `api/` ownership

Every HTTP feature domain is a package under `api/<domain>/`. Large domains split by real subdomain; small domains (`annotations`, `comments`, `find_replace`, `icon_markers`) use `routes.py` + `contracts.py`. Package `__init__.py` files compose routers only. The `api/` root itself contains only `__init__.py` and `shared_contracts.py`; do not add loose feature modules there. 10.2.27 freezes this backend layout for the frontend-refactor phase.

## `backend/` ownership

- `environment.py` — stable paths/version/runtime capability objects.
- `configuration.py` — read-only feature/Canvas/Metro configuration loaders.
- `content.py` — content type/color/sanitization primitives.
- `data_access.py` — tiny canonical SQLite row/reference helpers.
- `request_context.py` — project/actor binding, authorization, and hosted quotas.
- `portable_review_runtime.py` — standalone/temporary Portable Review process state.
- `recovery_runtime.py` — Recovery schedulers and backup-destination coordinator state.
- `history_runtime.py` — Remote History worker coordination and explicit History landmarks.
- `project_activation.py` — canonical project switching/activation boundary.
- `core.py` — thin FastAPI composition only.
- `lifecycle.py` / `local_history.py` — process lifespan and asynchronous local History publication.
- `http_boundary.py` / `request_pipeline.py` — HTTP security/error shell and request identity/project binding.
- `client_leases.py` / `danger_challenges.py` — process-local editor coordination and destructive confirmations.
- `api/project_management/` — profile/workspace/project administration HTTP surface.
- `backend/runtime_routes.py` — shell/runtime/session HTTP surface.

Feature modules should import the specific owner above (or another focused package owner) and must not use `backend.core` as a feature dependency namespace. HTTP request payloads live with their API domains; `api.shared_contracts` contains only the shared finite-number BaseModel and generic reorder payload.

## `api/durability/` ownership

The former mixed `api/history_backups.py` module is removed. `api.durability` composes focused owners for Cuts, immutable History, structural Undo, History baselines, Recovery/trash restore, Backup Destination configuration/providers, Recovery-backup scheduling, destination backup inventory/restore, and verified backup/integrity operations. Cross-feature callers target those submodules directly; `durability/__init__.py` does not re-export implementation internals.

## `workspace/` ownership

`workspace/__init__.py` is a lightweight stable boundary. `workspace.service` composes the single `Workspace` object; implementation belongs to `profiles`, `memberships`, `projects/`, `remote_history`, `credentials`, `backups/`, `erasure`, `providers`, `filesystem`, `models`, and `constants`. Production code that needs an internal workspace helper imports that owner directly rather than growing the package boundary.

Import/export HTTP ownership is under `api.import_export`, split across DOCX parser/import, bounded uploads, DOCX/PDF export, and assets. Workspace backup orchestration remains on the `Workspace` service but implementation ownership is split under `workspace.backups`.

Project JSON format ownership is split under `formats.project_json`: validation, inline markup, deterministic layout, database import, and database export have separate owners.
## Frontend ownership

Backend ownership is frozen at 10.2.27. Browser JavaScript ownership lives under `static/js/`; see `static/js/README.md`. As of 10.3.5 the JavaScript ownership migration is complete and the remaining mixed-owner files have been cleaned up: Reviews and Projects are packages, Manuscript history/workspace/persistence are separated from rendering, and feature-specific view preferences no longer live in `core/ui.js`. `static/app.js` is startup-only. As of 10.3.6 browser CSS ownership lives under `static/css/`, with `static/styles.css` limited to global foundation rules and `static/index.html` defining the canonical cascade order. 10.3.7 keeps that boundary fixed and removes only mechanically provable redundant cascade declarations. 10.3.8 adds an explicit shared-component reuse contract under `static/css/shared/COMPONENTS.md`; frontend feature code should reuse canonical control class stacks before creating new presentation grammar.


### 10.3.12 identity note

`auth/accounts.py` is hosted-only account identity mapping keyed by `(issuer, subject)`. Local runtime uses a null implementation and never creates account identity state. `auth/sessions.py` keeps opaque server-side sessions and now owns login rotation and user-level revocation primitives.


Auth UI theme parity applies only to the real Auth0 Universal Login page template. The Breadmaker-owned shell uses the Editor `light → dusk → dark` token sets and the same half-filled-circle theme control, while the Auth0-owned credential widget remains on the exact Breadmaker Light theme because Breadmaker does not rewrite Auth0 widget DOM. There is no local auth preview or preview runtime; adopting per-user runtime themes inside the Auth0 widget would require a separately reviewed ACUL boundary.
