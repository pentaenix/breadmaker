## 10.3.59 — Milestone 7A operator boundary

M7A is an opt-in hosted Identity Platform checkpoint, not Auth0 retirement. Preserve local accountless behavior and the frozen verifier/watchdogs. Identity Platform authority is server-side only: browser IDs/email are never authorization, provider tokens are never persisted, and private-alpha admission binds to the external UID. Keep Auth0 rollback intact through M7B; remove it only in M7C after the user verifies the migration gate. Delete All must remain able to erase local app data even if external backup erasure fails.

## 10.3.59 — Current rewrite boundary (Milestone 5B)

Milestone 5B is the behavioral-parity gate over the M5A cloud-native composition. Do not change the user-verified 10.3.52 verifier scheduling/watchdogs, `tests/conftest.py`, local runtime, local requirements, or the frozen Milestone-3/4 adapter implementations to make hosted tests pass. Hosted product behavior belongs above those adapters in `src/hosted/domain_store.py`, `src/hosted/runtime_services.py`, `src/hosted/http_api.py`, `src/hosted/http_compat.py`, and `src/hosted/packages.py`.

M5 invariants: canonical hosted mutations are Firestore/GCS-authoritative; Firestore-only batches must be atomic; immutable object deletion removes authority pointers rather than mutating object generations; per-member state requires project membership; route presence is not enough—major product flows must have credential-free conformance coverage. Keep Milestone-8 horizontal-safety work and Milestone-9 backup/recovery/purge work out of this release. The model/agent must not run the verifier/test suite.

## 10.3.53 — Current rewrite boundary (Milestone 5A)

Milestone 5A is the first cloud-runtime integration checkpoint. Treat the verified Milestone-3 Firestore repository implementation and Milestone-4 Cloud Storage publication implementation as frozen. Product-level hosted behavior belongs in `src/hosted/domain_store.py`, `src/hosted/runtime_services.py`, `src/hosted/http_api.py`, `src/hosted/packages.py`, and `src/runtime/hosted.py`. Do not make Firestore pretend to be SQLite and do not route hosted requests through local Workspace/project-directory persistence. All 287 current manifest routes are present at the hosted boundary, but do not treat route presence as behavioral parity; that remains the next M5 submilestone.

The all-green 10.3.52 verifier is a protected migration firewall: do not alter verifier scheduling, watchdog budgets, browser/E2E execution architecture, `tests/conftest.py`, or the local runtime merely to make hosted integration easier. Local Breadmaker remains Schema 42 and accountless/cloud-free. Hosted committed canonical state must survive process loss because Firestore/GCS, not process memory or server disk, is authoritative.

## 10.3.52 — Current rewrite boundary

10.3.52 is a Milestone-4 verification corrective. The 10.3.50 run confirmed the hosted image/security correction and left only six repeat Core/API watchdog expirations plus one Find pending-state race. Keep hard per-test watchdog budgets fixed; remove verifier contention instead of stretching timeouts. Find must never present `0 / 0` as a completed result while a non-empty query is still debouncing/flushing/searching.

Milestone 4 implements the server-only Cloud Storage content/revision/asset adapter behind the frozen Milestone-2 data model and the Milestone-3 Firestore repositories. `src/hosted/storage` owns server-generated object keys, generation-preconditioned object mutations, exact-generation SHA-256 readback verification, pointer-last Firestore publication, deterministic orphan discovery, and the hosted logical `AssetStore` implementation. Release contracts use `MemoryObjectStore`; the real Google Cloud Storage SDK is isolated to `src/hosted/storage/google_driver.py` and hosted requirements only. The active hosted product/runtime composition is deliberately **not** rewired yet; that is Milestone 5. Local Schema 42, SQLite/filesystem persistence, `LocalAssetStore`, accountless `./run`, and zero-cloud local dependencies remain protected. The model/agent must not run the verifier/test suite; the user does.

## 10.3.47 — Previous rewrite boundary

Milestone 3 implemented the server-only Firestore structured-state adapter behind the frozen Milestone-2 data model. Google SDK imports stay inside `src/hosted/firestore`; release tests use the deterministic memory driver and require no ADC/live project. Generation CAS, idempotent operation replay, project/member authorization, provider-independent `usr_…` users, and missing-vs-forbidden project equivalence are explicit contracts.

## Serverless hosted rewrite program

The current hosted/cloud rewrite is governed by `../SERVERLESS_HOSTED_REWRITE_PLAN.md` and `../SERVERLESS_HOSTED_REWRITE_TRACKER.md`. **Read the tracker first before hosted/cloud work.** The local product remains first-class SQLite/filesystem/accountless. The target hosted architecture is Cloud Run + Firestore + Cloud Storage + Identity Platform + Firebase Hosting, with no staging environment initially. The model/agent must not run Breadmaker `./verify-release`, `./test`, pytest, Playwright, or the test suite; the user runs verification and returns the compact result. Update the tracker continuously during milestones, not only at milestone completion.

## 10.3.41 — Current release boundary
Checkpoint 12 adds one distinct `hosted + staging + auth0` composition while keeping `./run` local, accountless, and zero-secret. Production hosted activation remains forbidden. Staging secrets are symbolic same-project Secret Manager references resolved through ADC; the four reviewed secret IDs are allowlisted. Hosted staging is exactly one process, requires HTTPS, Secure session cookies, and public-Host pinning. Never route staging through `hosted_dev`, and never add a secret requirement to local startup.
Selection toolbar hover UI must be positioned before reveal; delayed tooltips remain enter/leave-liveness owned rather than timer-only.


## 10.3.27 — Checkpoint 10.6 corrective verification

Schema 42 remains intentionally current-only. This corrective release updates stale historical verification contracts for immutable project identity, restores release/cache synchronization, and adds a visual-only localhost Auth UI preview without weakening the real Auth0 sandbox.

## 10.3.26 — Checkpoint 10.6: stable project identity and hosted project URLs

Schema 42 intentionally breaks every earlier pre-alpha project. Each current project now stores one immutable 32-hex `project_uid` inside SQLite; it must exactly match the portable manifest `project_id` and workspace folder/registry id. There is no migration or compatibility path. Hosted project URLs use `/p/:projectId` (plus `/canvas`, `/maps`, `/tasks`, `/reviews` suffixes), direct links resume the requested authorized project, and the hosted root resumes the account's last-opened project. An account with zero projects receives an in-memory blank manuscript; the first meaningful write creates the project, assigns its ID, persists the draft, and replaces the URL. The existing All Projects modal remains the only project browser. Local Breadmaker keeps its open-and-write behavior and does not gain authentication.

## 10.3.25 Checkpoint 10.5 UI ownership

Authentication UI must remain a presentation layer over Auth0 and the hosted security services. Never accept passwords/TOTP/provider tokens into Breadmaker, never use email as account-link authority, and never show hosted account/security controls in local runtime. Project URL IDs are Checkpoint 10.6.

## 10.3.24 Checkpoint 10 security-operation ownership

Do not move passwords, TOTP secrets/codes, recovery codes, Auth0 Management tokens, or step-up grants into browser/app state. Sensitive account mutations require fresh Auth0 proof and a server-only operation+target grant. Never allow primary/last-identity unlink, email-authorized identity changes, cross-user session revocation, database-TOTP disablement, or local-only claims of “sign out everywhere.” Security UI is intentionally absent until Checkpoint 10.5.

## 10.3.23 account-linking ownership

`src/auth/linking.py` is the sole owner of Checkpoint 9 linking. Never discover/link by email, accept primary/secondary Auth0 user ids from browser input, reuse the normal Auth0 application secret as Management API authority, or silently merge two existing Breadmaker users/projects. Both identities must be independently proven and a conflicting secondary mapping fails closed. Linking UI belongs to Checkpoint 10.5 after Checkpoint 10 security operations.

## 10.3.22 identity ownership

Do not implement passwords, TOTP generation/verification, recovery-code storage, or provider-token persistence in Breadmaker. Auth0 owns those credentials and MFA ceremonies. `src/auth/database.py` owns verified database-identity mapping and `src/auth/hosted_login.py` owns the Auth0 browser handoff/dispatch. Normal local runtime must remain accountless. Development Auth0 routes use only `hosted_dev + auth0 + development`; Checkpoint 12 staging routes use only `hosted + auth0 + staging`. Production remains forbidden until a later checkpoint.

## 10.3.21 hosted identity ownership

`src/auth/apple.py` owns Checkpoint 6 Apple-specific identity mapping. It may consume only a `VerifiedOidcIdentity` already produced by the generic Auth0/OIDC boundary, require the `apple|` strategy prefix, preserve the full Auth0 issuer+subject key, and preserve first-login metadata when a later Apple authorization omits it. Never identify/link by email, force users away from Apple private relay, accept Apple credentials/provider tokens into this mapper, or wire hosted login routes/UI during this checkpoint. `src/auth/google.py` remains the Checkpoint 5 owner and `src/auth/auth0.py` remains the broker/application boundary.

## 10.3.18 hover reliability rule

For delayed hover UI, use enter/leave-owned liveness state and bounded observable-state tests. Do not make delayed callbacks depend on `element.matches(':hover')` when the event lifecycle already owns cancellation.

## 10.3.17 corrective contract rules

Do not restore historical tests by reverting security fixes. Route contracts must use FastAPI's supported effective route/OpenAPI surface rather than internal flat-route assumptions. Preserve local image-format UX separately from the hosted PNG/JPEG/WebP trust boundary; server validation is authoritative. Local Breadmaker remains authentication-free and hosted login remains disabled through Identity Checkpoint 3.

## 10.3.16 verification tooling

Do not weaken security dependency pins to accommodate an obsolete verifier virtual environment. Release gates require Python 3.10+ and must rebuild stale `.venv` state instead.

## 10.3.15 image-security rules

Treat every uploaded or packaged image as hostile. Hosted code may accept only PNG/JPEG/WebP through `security.image_security`; do not add HEIF/HEIC/AVIF, ImageMagick, libheif, pillow-heif, or any new in-process decoder. Do not trust extension or MIME. Do not raise the 25 MiB hard ceiling, 8192-axis ceiling, or 20 MP geometry ceiling without a documented security review. Hosted decoding may be enabled only through the separately isolated worker contract in `SECURITY.md`; never decode project images in the main web process.

## 10.3.14 security rules

For HTTP security decisions, use `security.http_request.canonical_request_path(request)` / the ASGI scope path, never `request.url.path`. Do not weaken the outer streamed hosted body limit. Do not add runtime `eval`, `exec`, pickle deserialization, shell-mode subprocesses, or `os.system`. Production/security-critical dependencies are deliberately pinned and the release verifier runs `pip-audit`; do not downgrade or bypass the audit to make verification green. See `SECURITY.md`.

## 10.3.20 hosted identity ownership

`src/auth/oidc.py` remains the canonical provider-neutral OIDC/BFF protocol owner. Auth0 application configuration belongs only in `src/auth/auth0.py`; Google-specific identity mapping belongs only in `src/auth/google.py`. Do not leak Auth0/Google semantics into the generic engine. Google mapping must consume only verified OIDC identities, keep the full Auth0 issuer+subject key, reject non-`google-oauth2|` subjects, and never merge by email. Google Drive/API authorization is a separate capability grant and must not enter hosted-login scopes. Checkpoint 5 still exposes no hosted login routes or login UI. Local runtime must never instantiate hosted identity adapters.

# Breadmaker — Agent Navigation Contract

Breadmaker is intentionally organized so a coding agent should **locate the owning domain before reading implementation code**. Do not begin by scanning the full repository.

## Start here

1. `src/api/CODEMAP.md` — human-readable HTTP/domain map.
2. `src/api/route_manifest.json` — machine-readable method/path → handler map.
3. `docs/CODE_INDEX.json` — generated Python/JavaScript symbol → file/line index.
4. `ARCHITECTURE.md` — invariants and persistence/security boundaries.

When you know a symbol name, use an exact search (`rg "def symbol_name"`, `rg "function symbolName"`) before broad semantic search.

## Backend ownership

`src/` root is intentionally boring: only `__init__.py`, `app.py`, and `server.py` live there. Runtime/domain implementation belongs to an explicit package; do not add new loose root modules.

- `src/app.py` — composition only.
- `src/server.py` — outer ASGI/server boundary; delegate to the composed app, but import bootstrap/hosted-limit concerns from their canonical owners rather than through `backend.core`.
- `src/backend/` — lifecycle/platform coordination and diagnostics.
- `src/auth/` — identity and opaque-session primitives.
- `src/security/` — content sanitization and hosted guardrails.
- `src/storage/` — canonical project persistence and schema. Storage implementation is decomposed by responsibility and has no `storage.core` facade. Start with `src/storage/README.md` and import the canonical owner directly.
- `src/workspace/` — installation/project workspace orchestration. Start with `src/workspace/README.md`; `__init__.py` is boundary-only and implementation lives in focused owner modules.
- `src/formats/` — `.breadmaker`/fragment/`.breadreview` serialization contracts.
- `src/runtime/` — local/hosted runtime adapters.
- `src/backups/` and `src/revision_store/` — durability/history systems.

`src/backend/core.py` is composition-only: it creates the FastAPI app and installs focused backend owners. Lifecycle, middleware, local History, leases, destructive-confirmation state, request binding, and shell/runtime routes live in canonical backend modules. Profile/workspace/project-administration HTTP routes live in `src/api/project_management/`; endpoint implementations do not belong in `core.py`.

Feature routes live under `src/api/`:

- `project_management/` — platform Project Center HTTP package: profiles, workspace/identity state, project metadata/lifecycle/members, destructive actions, native package transfer, public JSON transfer, assets, and health. Registered by `backend.core` to preserve platform route order; `__init__.py` is composition only.
- `manuscript/` — Manuscript domain package. Use the narrow owner: navigation/bootstrap, document/basic Chapter metadata, image blocks, anchoring/repair, Chapter structure/mutations, or Scenes. `manuscript/__init__.py` is router composition only.
- `annotations/` — labels/text annotations and their request contracts. Cross-domain annotation/comment helpers live in `annotations.routes`; package boundary is router-only.
- `comments/` — manuscript comment threads/messages and their request contracts; package boundary is router-only.
- `reviews/` — Reviews domain package. Use the narrow owner: lifecycle, snapshots, portable packages, reader sessions, questionnaires, feedback/suggestions, participants, markers, or shared domain primitives. `reviews/__init__.py` is router composition only.
- `tasks/` — Tasks domain package. Use `lifecycle.py` for Task CRUD, `milestones.py` for milestone writes/order, and `duplication.py` for duplication. `tasks/__init__.py` is router composition only.
- `canvas/` — Canvas domain package. Use the narrow owner: Populate/templates, developmental synchronization, Canvas registry/lifecycle, Structure Versions, nodes, connectors, or manuscript-structure apply. `canvas/__init__.py` is router composition only.
- `icon_markers/` — reusable icon markers and request contracts; package boundary is router-only.
- `maps/` — Story Maps domain package. Use the narrow owner: anchor/projection services, Map lifecycle, Lines, Stops/Spans, Junctions, layout, or Populate/source sync. `maps/__init__.py` is router composition only.
- `workspace_tools/` — composition-only package: universal references, bookmark, collaborator UI state, and additional document lifecycle.
- `find_replace/` — find/replace routes and request contracts; package boundary is router-only.
- `checkpoints/` — Checkpoints domain package. Use the narrow owner: snapshot capture/storage, Chapter lineage/identity, comparison, restore support, manuscript restore, lifecycle routes, or preflight/apply. `checkpoints/__init__.py` is router composition only.
- `durability/` — durability HTTP package. Use the narrow owner: Cuts, immutable History, structural Undo, Recovery, Backup Destinations, Recovery-backup scheduling/inventory, or verified backup operations. `durability/__init__.py` is router composition only; the retired `history_backups.py` mixed domain must not return.
- `import_export/` — DOCX parsing/import, DOCX/PDF export, upload limits, and generic assets with explicit owners.

### Explicit backend dependencies (10.2+)

The transitional 10.1 namespace bridge is gone. Feature modules import platform concerns from their canonical owners (`backend.content`, `backend.environment`, `backend.data_access`, `backend.request_context`, domain-owned `contracts.py` modules, storage/auth/security/etc.). Stateful platform concerns have focused owners as well: `backend.portable_review_runtime`, `backend.recovery_runtime`, `backend.history_runtime`, and `backend.project_activation`. Feature modules **must not import `backend.core`**. Cross-feature calls use `feature_call("owner", "symbol")`, which resolves the canonical function lazily and avoids import-time cycles. `src/api/dependency_manifest.json` is the machine-readable ownership graph.

**Dependency rule:** do not add broad namespace injection, `globals().update`, `setdefault` binding, a replacement service locator, or feature-owned mutable state shared across router modules. Reassignable process state has one canonical owner and must be read or mutated through that owner/accessor/service. If two domains need shared logic rather than a simple call, move that logic downward into a stable backend/runtime/storage service.

New code should import the narrowest stable module directly. Do not route a feature dependency through `backend.core` merely because `core.py` participates in application assembly. The same rule applies inside `workspace`: internal helpers/constants/errors come from their owner (`workspace.filesystem`, `workspace.constants`, `workspace.models`, etc.), not from new package-level compatibility aliases. Platform-sensitive or intentionally patchable cross-owner helpers should be resolved through that owner module at call time rather than copied into a consumer module global.

## Dependency direction

Prefer:

`server.py → app.py (composition) → backend.core (thin composition) → focused backend owners + api/<domain owner> → auth/security/runtime/storage/workspace services`

Avoid feature-to-feature imports when a lower-level runtime/storage service is the real dependency. If two domains need the same helper, move that helper downward rather than creating a circular router dependency.

## Naming and discoverability rules

- Route handler names are stable verbs/nouns (`create_canvas`, `populate_map`, `save_blocks`).
- Domain-private helpers begin with `_` and stay near their owning route/domain.
- Do not create generic files named `utils.py`, `helpers.py`, or `misc.py` for domain logic.
- New HTTP routes must live in the domain module that owns the URL/resource.
- If a new domain becomes large enough to need submodules, create `src/api/<domain>/` rather than another monolith.
- Regenerate `docs/CODE_INDEX.json` after adding/removing/renaming symbols: `python scripts/generate_code_index.py`.
- Regenerate the route map/manifest after route changes: `python scripts/generate_api_map.py`.

## Critical product invariants

- Local Breadmaker remains accountless, offline-capable, and must not require Auth0/cloud access.
- Hosted Breadmaker requires server-authenticated identity and project membership.
- Browser-provided user IDs never establish hosted identity.
- Project/tenant context is request scoped; never fall back to another user's active project.
- Canonical Current commits immediately; History/backup work must not block interactive editor hot paths. `backend.local_history` owns and registers the non-blocking canonical-commit observer with storage.
- Imported/rendered HTML and asset references cross explicit trust boundaries.
- Pre-alpha policy: do not add legacy schema migrations unless explicitly requested.

## Before changing behavior

Read the domain module, the relevant storage/runtime implementation, and the tests named after that subsystem. Prefer extending behavioral tests over adding string-position tests tied to a particular file layout.
## Canonical backend imports (10.2.4)

After the backend package layout cleanup, import implementations from their owning module: for persistence choose the specific `storage.*` owner; for authentication use `auth.sessions` / `auth.identity`; for security use `security.content_security` / `security.hosted_guardrails`; for portable Review use `formats.breadreview`. Do not add compatibility facades merely to preserve an old internal import path, and do not turn package `__init__.py` files into broad re-export namespaces.


## 10.2.26 final backend ownership cleanup

- `backend/contracts.py` is removed. HTTP payloads live beside their owning API domain; only `api.shared_contracts` contains the finite-number BaseModel and generic `Reorder`.
- `api/workspace_tools/` owns references, bookmark, UI state, and document lifecycle independently.
- `formats/project_json/` owns validation, markup, layout, import, and export independently.
- `workspace/projects/` owns registry/recovery, package transfer, and trash independently.
- Pre-alpha rule: do not recreate compatibility facades for any of these retired flat modules.

## 10.3.x frontend architecture phase

The backend is frozen at the 10.2.27 ownership layout. Frontend JavaScript is decomposed under `static/js/` while remaining build-step-free. Treat `static/js/core/` as shared browser infrastructure and `static/js/features/` as feature ownership. As of 10.3.5 mixed-owner frontend files are also decomposed: Reviews and Projects are package-owned, Manuscript rendering does not own history/workspace/persistence, and feature-specific view/bookmark behavior does not belong in `core/ui.js`; generic modal/toast/context-menu infrastructure does. Shared typography primitives belong in `core/typography.js`. `static/app.js` is a startup shim only; never add product behavior or shared services there. When frontend source contracts need implementation text, use `frontend_source()` rather than a physical file path. As of 10.3.6 CSS is decomposed under `static/css/`; `static/styles.css` is the global foundation owner only. The stylesheet order in `static/index.html` is part of the runtime contract. 10.3.7 establishes the cleanup rule: remove historical CSS only when redundancy is mechanically provable (for example, a later identical selector/declaration reassertion); do not infer dead selectors from source absence or remove specificity/`!important` heuristically. Use `frontend_styles_source()` (or the historical-path adapter) for CSS source contracts. See `static/css/README.md`. As of 10.3.8 frontend work is also **reuse-before-create**: before adding a control, inspect `static/css/shared/COMPONENTS.md` and existing shared selectors; reuse the canonical base class/markup when semantics match, keep feature classes as behavior/state hooks or narrow modifiers, and promote genuinely cross-workspace grammar to `static/css/shared/` instead of cloning declarations. The shared compact toolbar-icon primitive is `tool-icon-button`; do not recreate the retired/misnamed `manuscript-tool-button`.

## 10.3.11 delayed-hover reliability

The architecture freeze remains in force. For delayed hover UI, use the owning enter/leave (or focus) state as the liveness token; do not re-sample `:hover` inside the delayed callback when the corresponding leave handler already invalidates that token. Browser tests should wait for the bounded observable state instead of assuming JavaScript timers execute within a tiny scheduling margin.

## 10.3.10 documentation-contract correction

10.3.10 does not reopen the architecture freeze. Preserve the normative CSS cleanup rule exactly: **do not infer dead selectors from source absence**. Static absence is not sufficient evidence that dynamically generated UI selectors are dead.

## 10.3.9 frontend architecture freeze

The frontend ownership refactor is complete. Application-wide Undo/Redo and structural transactions belong to `static/js/core/history.js`; Manuscript snapshot capture/restoration belongs to `static/js/features/manuscript/history.js`; primary-view discovery belongs to `core/routing.js`; generic compact collection-rail helpers belong to `core/collections.js`; and feature-specific wrappers remain with their feature. Manuscript UI-state restoration belongs to `features/manuscript/view.js`, not Canvas. Cuts synchronization belongs to `features/durability/history.js`.

Do not reopen frontend decomposition simply to reduce line counts, eliminate globals, or make the tree more fashionable. Keep the build-step-free ordered classic-script model, the 22-file CSS cascade order, and the reuse-before-create component contract unless a concrete product requirement proves a boundary is wrong. When adding UI, inspect `static/css/shared/COMPONENTS.md` first and reuse canonical primitives.

## 10.2.27 backend architecture freeze

The backend decomposition is complete. Every HTTP feature is a package under `src/api/<domain>/`; small domains may contain only `routes.py`, `contracts.py`, `README.md`, and a router-only `__init__.py`. The `src/api/` root must remain limited to composition/shared primitives. Do not reopen backend decomposition merely to reduce file length; only introduce a new owner when a concrete responsibility no longer fits an existing cohesive module. As of 10.3.9 the frontend ownership phase is frozen by the same rule.

## 10.3.12 hosted identity boundary

- Local Breadmaker must remain authentication-free. Never require a Breadmaker account, entitlement, or network identity in `BREADMAKER_RUNTIME=local`.
- `runtime.accounts` is `NullAccountIdentityService` locally and a hosted-only `(issuer, subject)` registry only in account-session hosted modes.
- Email is metadata, never an account lookup/merge key. Do not add email-based automatic linking.
- Establish authenticated sessions with replacement/rotation so a pre-authentication cookie cannot survive login.
- Checkpoint 4 may add Auth0 server-side boundary/configuration code only. Do not add provider login UI, Google/Apple/database/TOTP flows, or hosted login routes until their later checkpoints are green.
- Breadmaker Cloud Backup is a separate paid capability; connecting it must never convert the local editor to hosted account-session mode.

- Do not weaken, skip, ignore, or allowlist the `pip-audit --strict` release gate to make a vulnerable or unauditable dependency pass.


### Archive image ingress

The hosted image ceiling applies before image bytes are materialized from Cuts, portable BreadReview, and native project-package imports. Hosted project packages are rejected before activation if any `assets/` member exceeds the configured image ceiling or is not a structurally valid PNG/JPEG/WebP. Portable BreadReview decoding receives the same per-asset ceiling. These archive paths do not create a decoder exception.
