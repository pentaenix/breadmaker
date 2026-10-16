# How Breadmaker is built

Breadmaker has two application runtimes: one for a single computer and one for a hosted service. They share product code, but **not** databases, authentication, or infrastructure requirements.

```text
Local editor                              Hosted service
────────────                              ──────────────
Browser                                   Browser
   │                                        │ HTTPS + login
   ▼                                        ▼
Local API on 127.0.0.1                   Hosted API + access checks
   │                                        │
   ├── SQLite project data                  ├── Firestore records
   ├── Local images and assets              ├── Cloud Storage objects
   └── Local history / backups              └── Hosted recovery operations
```

## Local: a complete application without the cloud

`./run` starts the local FastAPI application, which serves the browser UI from `static/`. SQLite and the filesystem hold project data under `BREADMAKER_HOME` (default: `~/.breadmaker-editor/`). The local runtime does not create user accounts, call Identity Platform, or require a network connection for normal writing once Python dependencies are available.

The storage layer is under `src/storage/`; project creation, packages, profiles, and backup destinations are under `src/workspace/`. Feature endpoints live under `src/api/`. Frontend features live under `static/js/` and `static/css/`.

**A browser window is the interface, not the source of truth for saved manuscripts.** Work must be durably committed to the local project store. Local revision history, Undo, exported packages, and destination backups serve different purposes and should not be treated as interchangeable.

## Hosted: separate persistence and identity

The hosted runtime uses Firestore for project records, Cloud Storage for objects, and Identity Platform for sign-in. Breadmaker checks identity and project permissions on the server; a project or user ID sent by a browser is never, by itself, permission to access that data.

Hosting configuration, infrastructure, credentials, and deployment automation are maintained in the **private `pentaenix/breadmaker-cloud` repository**, using OpenTofu. The public Editor repository includes application source and deployment-facing contracts, not a production deployment or its secrets.

The normal hosted runtime uses server-readable manuscript data with Google-managed encryption at rest. Experimental application-layer encryption and sealed recovery code are available for specifically supported record families but are **not activated in the standard hosted runtime**. Their presence in the tree must not be interpreted as a live security guarantee.

## Source navigation

| Looking for… | Start here |
| --- | --- |
| HTTP endpoints and feature owners | [`src/api/CODEMAP.md`](src/api/CODEMAP.md) |
| Storage and SQLite integrity | [`src/storage/README.md`](src/storage/README.md) |
| Projects, packages, backups | [`src/workspace/README.md`](src/workspace/README.md) |
| Browser modules | [`static/js/README.md`](static/js/README.md) |
| Hosted trust boundaries | [`docs/HOSTED_DATA_TRUST_BOUNDARIES_M12.md`](docs/HOSTED_DATA_TRUST_BOUNDARIES_M12.md) |
| M12 private-source limitations | [`docs/security/M12_SOURCE_CLOSURE.md`](docs/security/M12_SOURCE_CLOSURE.md) |
| Verifying a release | [`docs/TESTING.md`](docs/TESTING.md) |

For pre-alpha changes, correctness is more important than compatibility with obsolete project formats. Unsupported data is rejected rather than silently migrated. The full test gate must pass before a release is promoted.

## Architecture history

The dated sections below record earlier design decisions, including systems that have since been removed. In particular, Auth0 and old staging instructions **do not describe the current hosted runtime**.

---

<details>
<summary>View detailed architecture history (historical engineering record)</summary>

## 10.3.61 — Milestone 7C current hosted identity authority

Local Breadmaker remains authentication-free, accountless, Schema 42, and SQLite/filesystem-backed. Hosted canonical state remains Firestore + Cloud Storage and Google Identity Platform / Firebase Authentication is the sole real hosted authentication authority. Browser provider state is proof only with in-memory persistence; a fresh provider proof is exchanged for Breadmaker's Secure/HttpOnly server cookie, and project/review authorization remains Breadmaker-owned. Email is never identity/link authority. Password identities require verified email plus TOTP, and sensitive account operations require fresh same-UID reauthentication.

Auth0 is retired from current runtime/IaC/operator authority. Historical sections below remain intentionally present because they document frozen product/storage/security contracts that later releases continue to preserve; they are history, not live Auth0 desired state. M8 owns Cloud Run lifecycle/horizontal safety and M10 owns the deployable Firestore/GCS/Cloud Run data plane.

## 10.3.59 — Milestone 7A Identity Platform runtime boundary

Hosted authentication gains a second provider-neutral composition backed by Google Identity Platform/Firebase Auth. Identity Platform UID is external identity authority; Breadmaker maps it to its stable internal user ID and never treats email or browser-supplied IDs as authorization. A fresh ID token (recent `auth_time`) may be exchanged for the server cookie only after private-alpha admission. Subsequent requests bind from the verified, revocation-checked provider session cookie. The Firebase Admin SDK is hosted-only and imported lazily from ADC; local runtime and local requirements remain provider-free.

M7A does not retire Auth0. Real staging startup remains Auth0 until M7B completes provider/MFA/linking/security UX and M7C removes Auth0 runtime/configuration. The local Delete-All path also detaches global storage before erasure so stale writers cannot recreate pre-reset project directories while the privacy barriers are held.

## 10.3.59 — Milestone 5B hosted behavioral parity

M5B completes the current hosted application-integration gate without changing the frozen Milestone-3 Firestore repositories, Milestone-4 object publisher, local runtime, or verifier harness. The hosted domain seam now owns atomic Firestore-only batch creation, pointer-only deletion for immutable object-backed entities, and authorization-aware per-member state. GCS generations remain immutable; deleting a checkpoint deletes its Firestore authority pointer and intentionally leaves object bytes as an orphan/retention candidate.

The hosted HTTP compatibility layer now uses the same source-controlled story-template catalog as local Breadmaker for Canvas/Map Populate, derives project-backed chapter/scene entries from Firestore, and persists Map auto-arrangement rather than claiming a no-op success. Manuscript chapter-block replacement removes omitted blocks, while hybrid prose remains object-first/pointer-last. The M5 conformance suite runs entirely against deterministic Firestore/GCS fakes and exercises the major flows required by the serverless rewrite plan.

Later-milestone boundaries remain deliberate: Cloud Run horizontal/process-loss safety is Milestone 8; backup/recovery/account purge/history restore is Milestone 9.

## 10.3.53 — Milestone 5A hosted runtime composition

M5A establishes the cloud-native composition and full hosted HTTP route surface (287/287 current manifest routes). Full behavioral parity remains the next Milestone-5 submilestone.

Hosted and local product composition now diverge intentionally at the FastAPI/runtime boundary. Local mode continues through `Workspace`, Schema-42 SQLite, native project/assets/history/backup bridges, and the existing local API routers. Hosted modes compose server-owned Firestore/GCS services and install a hosted-only product/platform router; no persistent server filesystem is part of hosted correctness.

The new `HostedDomainStore` is the Milestone-5 domain seam above the frozen Milestone-3 Firestore repositories and Milestone-4 object publisher. It owns project-generation advancement, authorization-aware collection access, and object-first/pointer-last hybrid entity publication without modifying those verified adapters. Real hosted mode uses Google provider drivers; `hosted_dev` uses the same interfaces with deterministic in-memory stores.

Hosted project packages are authority-neutral snapshots: memberships, account identities, provider object generations, and server-only metadata are not portable. Import creates a fresh 32-hex project ID and republishes object content under that identity. Legacy package-v2 SQLite bytes may be decoded with an in-memory SQLite connection solely as an import format bridge; SQLite never becomes hosted authority.

## 10.3.52 — Serverless rewrite Cloud Storage object boundary

10.3.52 is a verification corrective over the same Milestone-4 storage architecture carried through 10.3.50. It changes no hosted storage/runtime semantics. After the 10.3.50 user run proved the PNG/security and database-health corrections, the remaining work was verifier contention plus a browser Find pending-state race: six repeat timeout modules now use the existing one-worker no-contention Core/API lane, and Find exposes an explicit pending state while its debounced async request is in flight. Hard per-item watchdog budgets remain unchanged.

Milestone 4 adds the cloud-native object half of hosted persistence without changing the local engine or yet replacing the old undeployed hosted runtime composition. `src/hosted/storage` provides deterministic and Google Cloud Storage drivers with explicit generation preconditions, canonical server-generated keys, bounded streaming reads/writes, exact-generation SHA-256 verification, immutable revision publication, and retention-aware orphan discovery. `HostedHybridObjectPublisher` writes/verifies the object first and publishes authoritative Firestore revision/object metadata last under transaction/CAS; a failed pointer publication may leave only an orphan, while a successful pointer can never be emitted before object verification. `HostedAssetStore` implements the Milestone-1 logical asset port without filesystem paths and preserves the decoder-free hosted image policy. Google Cloud Storage remains hosted-only; local Schema 42, SQLite/filesystem persistence, local assets/history, and accountless `./run` are unchanged. Full hosted API/runtime composition remains Milestone 5.

## 10.3.47 — Serverless rewrite Firestore structured-state boundary

Milestone 3 turns the frozen hosted data model into a server-only structured-state adapter without changing the local engine. The adapter owns provider-independent users, identity bindings, projects, memberships, and structured project entities through transaction-scoped authorization, application generations/CAS, and idempotent operation replay. A deterministic memory driver mirrors the document/transaction contract for release verification; the Google Firestore client is a hosted-only boundary and requires no local dependency or credential. Direct browser Firestore access remains forbidden. Local SQLite/filesystem architecture remains unchanged.

## 10.3.43 — Serverless rewrite seam boundary

The local engine remains the reference SQLite/filesystem implementation on Schema 42. Hosted persistence is being separated behind environment-neutral ports rather than making cloud storage imitate local paths. Milestone 1 extracts project catalog/membership, logical asset bytes, usage/quota projection, and avatar reads; filesystem/package/history/backup dependencies remain explicit `Native*` migration debt and are frozen in `docs/HOSTED_LOCAL_DEPENDENCY_INVENTORY.json`. No hosted data model is invented until Milestone 2.

## Checkpoint 12 staging runtime (10.3.41)

`local` remains the default runtime and has no account or hosted-secret dependency. `hosted_dev` remains the explicit localhost integration sandbox. `hosted` is a distinct deployment composition and currently accepts only `BREADMAKER_ENV=staging` + `BREADMAKER_AUTH_MODE=auth0`; production is rejected. The staging runtime reuses the Checkpoint 11 OIDC/session/linking/step-up machinery unchanged and resolves only explicit same-project `secretmanager:projects/.../versions/...` references through ADC. Auth/session transaction authorities remain process-local, so staging is deliberately one process/worker until a shared atomic authority store exists.

## 10.3.37 — Checkpoint 11 frozen identity boundary
The hosted Auth0 architecture is feature-frozen for the pre-deployment adversarial gate. Schema 42 project IDs remain authorization-neutral, local Breadmaker remains authentication-free, and no preview-only authentication runtime is carried forward.

The local auth UI review surface is structurally separate from authentication: it exists only in `hosted_dev + developer`, serves a static non-authenticating rendering, and cannot create sessions or call Auth0/provider endpoints. Real hosted login still exists only in `hosted_dev + auth0 + development`. Locale switching in the real Auth0 flow returns through `/auth/language` using a ten-minute single-use opaque context; the page template never receives the project return path and cannot supply an arbitrary redirect target. Normal local Breadmaker remains accountless and Schema 42 is unchanged.

## 10.3.27 — Checkpoint 10.6 corrective verification

Schema 42 remains the only supported pre-alpha project schema; no migration or compatibility path was added. Importing a portable project creates a new immutable project identity, while content-equivalence verification normalizes only that intentionally rewritten identity. The development Auth UI preview is a non-authenticating visual surface and is structurally separate from the real Auth0 sandbox.

## 10.3.26 — Checkpoint 10.6: stable project identity and hosted project URLs

Schema 42 intentionally breaks every earlier pre-alpha project. Each current project now stores one immutable 32-hex `project_uid` inside SQLite; it must exactly match the portable manifest `project_id` and workspace folder/registry id. There is no migration or compatibility path. Hosted project URLs use `/p/:projectId` (plus `/canvas`, `/maps`, `/tasks`, `/reviews` suffixes), direct links resume the requested authorized project, and the hosted root resumes the account's last-opened project. An account with zero projects receives an in-memory blank manuscript; the first meaningful write creates the project, assigns its ID, persists the draft, and replaces the URL. The existing All Projects modal remains the only project browser. Local Breadmaker keeps its open-and-write behavior and does not gain authentication.

## 10.3.25 — Identity Checkpoint 10.5: hosted auth UI composition

The account avatar exposes Account & Security only when runtime capability `account_sessions` is present and auth mode is `auth0`. The UI consumes project-free `/api/auth/security` state and JSON prepare endpoints, then navigates to Auth0 for fresh proof. Normal local composition is unchanged. Auth/project URL identity remains outside this checkpoint and is planned for 10.6.

## 10.3.24 — Identity Checkpoint 10: security-service composition

`auth/security.py` is instantiated only by the explicit `hosted_dev + auth0` composition. It owns fresh step-up transactions, operation-scoped grants, Auth0 security-management calls, and signed MFA-enrollment intents. Account/security endpoints are project-context-free but still authenticated and same-origin protected. The normal local composition is unchanged and cannot instantiate Auth0 or the security service.

Checkpoint 10 deliberately contains no product UI. Checkpoint 10.5 will consume these backend contracts; Checkpoint 11 will adversarially review/harden the complete identity surface before Checkpoint 12 live staging smoke.

## 10.3.23 — Identity Checkpoint 9: account-linking boundary

Account linking is an explicit security operation, not identity discovery. The current authenticated Breadmaker user supplies the primary identity through the hosted account registry; a second independent Auth0 OIDC transaction forces fresh authentication and supplies the secondary identity. Browser input never supplies Auth0 user IDs and email never selects a candidate. A secondary already attached to another Breadmaker user is a hard conflict rather than an implicit account/data merge.

The Auth0 mutation uses a dedicated server-only `update:users` operator distinct from the hosted application client and deployment credentials. The local runtime remains permanently accountless. Checkpoint 10 owns recovery/step-up/security operations, and Checkpoint 10.5 will add their product UI.

## 10.3.22 — Identity Checkpoints 7+8 + localhost hosted-auth composition

The authentication composition now has three explicit launch shapes: ordinary `./run` is permanently local/accountless; `./run-hosted-dev` is the fixed developer-identity hosted persistence target; and `./run-hosted-auth-dev` is a real Auth0 development sandbox using the hosted session/account boundary. There is no UI switch that can mutate local Breadmaker into authenticated mode.

The localhost Auth0 composition is allowed only when `BREADMAKER_RUNTIME=hosted_dev`, `BREADMAKER_AUTH_MODE=auth0`, and `BREADMAKER_ENV=development`. It uses an isolated data home and exact loopback `/auth/callback`. Staging/production hosted login remains disabled. The callback verifies Auth0 through the existing generic OIDC engine, accepts only Google, Apple, or Auth0-database subject families, resolves `(issuer, subject)`, rotates the Breadmaker opaque session, and creates the account's initial project if required.

Checkpoint 7 adds the database mapper and requires verified email before Breadmaker session creation. Checkpoint 8 deliberately keeps TOTP entirely in Auth0: database identities must enroll/challenge OTP; social identities are not forced to enroll, but an already-enrolled OTP becomes sticky and is challenged on later logins. Breadmaker stores no password, TOTP secret, OTP, recovery code, provider token, or Auth0 Management API credential. Account linking and Security UI remain later checkpoints.

## 10.3.21 — Identity Checkpoint 6: Apple provider mapping

`src/auth/apple.py` is the Apple-specific identity projection behind `src/auth/auth0.py` and provider-neutral `src/auth/oidc.py`. The generic OIDC engine verifies the Auth0 token first; the Apple mapper then requires the verified Auth0 subject to use the `apple|` strategy prefix and preserves the complete Auth0 `(issuer, subject)` tuple as Breadmaker's external identity key. Dotted Apple provider identifiers are valid; email is never identity or linking authority.

Apple privacy is preserved by design. A user may keep a real address private; both `privaterelay.appleid.com` and the newer `private.icloud.com` Sign in with Apple relay domains are valid metadata. Apple may provide name/profile material only on initial authorization, so omission on later logins must not clear previously stored metadata. Breadmaker does not receive Apple signing keys or retain Apple provider access/refresh tokens.

No login/callback HTTP route is enabled in this checkpoint; **hosted login remains disabled**. Local Breadmaker remains authentication-free and does not instantiate hosted identity providers.

## 10.3.20 — Identity Checkpoint 5: Google provider mapping

`src/auth/google.py` is the Google-specific identity projection behind `src/auth/auth0.py` and the provider-neutral `src/auth/oidc.py`. The OIDC engine must verify the Auth0 token first. The Google mapper then requires the verified Auth0 subject to use the `google-oauth2|` strategy prefix and preserves the entire verified Auth0 `(issuer, subject)` tuple as Breadmaker's external identity key. It never derives identity from email and never receives Google access/refresh tokens.

Google login identity scopes remain `openid profile email`. Google Drive and any other Google API authorization are separate capability grants and must not be added to hosted-login scopes. No login/callback HTTP route is enabled in this checkpoint; **hosted login remains disabled**. Local Breadmaker remains authentication-free and does not instantiate hosted identity providers.

## 10.3.19 — Identity Checkpoint 4: Auth0 boundary

`src/auth/auth0.py` is the provider-specific edge around the provider-neutral `OidcClient`. The boundary carries only non-secret Auth0 application metadata and a symbolic client-secret reference; the actual secret is supplied by a caller-owned runtime secret loader immediately before constructing the OIDC client. It validates exact application origin/callback semantics, allows loopback HTTP only in development, requires HTTPS for staging/production, fixes the hosted scope set to `openid profile email`, and does not expose Auth0 Management API authority.

**Hosted login remains disabled.** Checkpoint 4 adds no login/callback route and no Google, Apple, database, TOTP, linking, or login UI behavior. Local Breadmaker remains authentication-free and never instantiates the Auth0 adapter. Breadmaker Cloud Backup remains a separate future paid capability and does not convert local editing into hosted account-session mode.

## 10.3.18 — marker hover reliability corrective

This corrective release removes a scheduler-sensitive delayed `:hover` query from the marker panel and makes the durable test wait for observable state. It does not reopen the frontend architecture freeze, identity architecture, security boundary, or hosted image policy. Local Breadmaker remains authentication-free and hosted login remains disabled.

## 10.3.17 — security-contract reconciliation

This corrective release reconciles historical tests with the patched FastAPI and hostile-image boundaries without weakening either boundary. FastAPI route-registration verification uses the public OpenAPI path-operation surface instead of assuming `app.routes` is a flat list. Local image selection regains its historical browser-supported formats, while hosted runtime attestation narrows Manuscript and Canvas file pickers plus Manuscript drag/drop to PNG/JPEG/WebP; the hosted backend remains the authoritative structural validator. Two identity invariants remain explicit: local Breadmaker remains authentication-free, and hosted login remains disabled. Identity implementation remains at Checkpoint 3.

## 10.3.16 — verifier interpreter boundary

Release verification now treats Python 3.10+ selection as a reproducible tooling boundary. A stale `.venv` created by an older interpreter is disposable verifier state and must be rebuilt rather than reused. This changes no local or hosted application runtime behavior.

## 10.3.15 — hostile-image ingestion boundary

Hosted project images cross a narrow non-decoding boundary in `security.image_security`: PNG/JPEG/WebP only, structural/magic verification, 12 MiB default encoded size (25 MiB hard configurable maximum), 8192 px per axis, and 20 MP total geometry. HEIF/HEIC/AVIF are explicitly outside the hosted format set. Project assets imported through Cuts/portable Review paths and asset reads are subject to the same hosted validation. The main hosted process must not decode project images; PDF/DOCX exports substitute placeholders until an independently isolated ephemeral image worker is implemented. Local mode remains unchanged.

## 10.3.14 — hosted dependency and HTTP request security boundary

Internet-facing security decisions use `security.http_request.canonical_request_path()`, which reads ASGI `scope["path"]`; middleware must not use `request.url.path` for authorization, rate limiting, body-limit selection, project binding, or other trust decisions. The production dependency graph pins patched FastAPI/Starlette/Uvicorn, multipart parsing, ReportLab, cryptography, Authlib and JOSERFC versions, and `verify-release` performs a fail-closed known-vulnerability audit before the normal suite. The outer ASGI streamed-body limiter remains authoritative for chunked/unknown-length requests. This corrective release does not enable hosted login and does not alter local authentication-free operation.

## 10.3.13 — Identity Checkpoint 3: generic OIDC protocol boundary

Checkpoint 3 adds `src/auth/oidc.py` as a dormant, provider-neutral BFF protocol owner. It implements Authorization Code + PKCE S256, single-use server-side state/nonce transactions, discovery, confidential server token exchange, JWKS/RS256 ID-token verification, exact issuer/client audience checks, azp/time validation, and one refresh on unknown signing key. **Hosted login remains disabled**: no `/auth/login` or `/auth/callback` route exists, no Auth0 provider is wired, and the local runtime still uses null account/session services and never depends on network identity. The deterministic fake issuer/JWKS test harness is the only Checkpoint 3 consumer.

## 10.3.12 — hosted identity/session foundation (Checkpoint 2)

Checkpoint 2 adds only the internal hosted identity and session primitives required before OIDC exists. External account identity is stored in a hosted-only registry with a unique `(issuer, subject)` key and a stable Breadmaker `usr_…` user id. Email remains non-authoritative metadata, so equal email strings never cause automatic account merging. Session authentication now supports replacement/rotation, user-wide revocation, bounded idle/absolute expiry, non-secret session enumeration, and server-derived `authenticated_at` timestamps.

**Local Breadmaker remains authentication-free.** `BREADMAKER_RUNTIME=local` continues to use null account/session services and never touches the hosted identity registry. Local opening, editing, saving, recovery, profiles, and existing backup integrations require no Breadmaker account or subscription. Breadmaker Cloud Backup remains a distinct future paid service connection and may not switch the local runtime into account-session mode. Hosted login remains disabled: there is still no Auth0/OIDC adapter, Google/Apple login, password/TOTP flow, or login UI.

## 10.3.11 — reliability patch after architecture freeze

10.3.11 does not reopen the frontend architecture freeze. It hardens one concrete interaction discovered by the durable E2E run: delayed checkpoint hover is owned by explicit enter/leave state, so a delayed callback does not depend on re-sampling the browser's `:hover` pseudo-state. Browser tests for delayed UI should wait on the bounded observable state rather than rely on a narrow wall-clock margin around a JavaScript timer.

## 10.3.10 — documentation contract correction

10.3.10 is a documentation-only corrective release after the 10.3.9 frontend architecture freeze. It restores the normative 10.3.7 CSS cleanup rule verbatim: **do not infer dead selectors from source absence**. No runtime ownership, CSS cascade, schema, persistence format, API contract, or intended product behavior changes.

## 10.3.9 — final frontend ownership cleanup and architecture freeze

The frontend ownership phase is complete. Application-level history coordination (`performUndo()`, `performRedo()`, `withStructuralUndo()`, history stacks, structural rollback/reopen coordination) is owned by `static/js/core/history.js`; Manuscript-specific edit snapshots remain in `features/manuscript/history.js`. Primary-view discovery (`currentView()`) belongs to `core/routing.js`. Generic compact collection-rail labeling and quick-add construction live in `core/collections.js`, while Canvas and Maps own only their domain-specific label wrappers. Manuscript UI-state restoration is owned by `features/manuscript/view.js`, and Cuts synchronization is owned by `features/durability/history.js`.

This is the **frontend architecture freeze**. The backend remains frozen at 10.2.27, `static/app.js` remains startup-only, the ordered classic-script model remains intentional, the 22-file CSS cascade boundary remains stable, and `static/css/shared/COMPONENTS.md` remains the reuse-before-create contract. Do not introduce new owners, split cohesive files, replace classic scripts with a framework/bundler, or reorganize CSS merely for line-count or aesthetic reasons. Reopen architecture only when a concrete new responsibility no longer fits an existing owner or a product defect demonstrates a bad boundary.

## 10.3.8 — shared frontend component contract

The backend, JavaScript ownership map, and 22-file CSS cascade boundary remain frozen. 10.3.8 adds the missing reuse contract on top of those boundaries: shared interaction/presentation primitives are cataloged in `static/css/shared/COMPONENTS.md`, feature code must reuse their canonical DOM/class grammar before creating a new component, and feature classes should represent behavior/state/narrow modifiers rather than copied geometry. Cross-workspace grammar belongs under `static/css/shared/`; visual similarity alone is not sufficient to promote a feature-specific component.

The first concrete ownership corrections rename the shared compact toolbar-icon primitive from `manuscript-tool-button` to `tool-icon-button`, reflecting its actual use across Manuscript, Canvas, Maps, Reviews, and Reviewer Mode, and move the generic `openContextMenu()` renderer from Manuscript Scenes into `core/ui.js`. Features still own menu item definitions; core UI owns generic rendering/positioning/submenu behavior. Historical contracts move to canonical shared ownership rather than preserving misleading feature-owned vocabulary. The release verifier also separates `last fail` (a completed failing item) from `oldest` (a still-running job), preventing concurrent progress output from implying that the oldest active module caused a failure.

## 10.3.7 — conservative CSS cascade cleanup

The 10.3.6 CSS ownership tree is now the stable frontend stylesheet boundary. 10.3.7 cleans historical cascade debt without changing that ownership map or stylesheet order. The cleanup rule is deliberately narrow: an earlier top-level declaration is removable only when a later rule with the exact same selector declares the exact same property, serialized value, and importance state. Under a fixed ordered stylesheet set, the earlier declaration cannot determine the final computed value, so the cleanup removes 205 declarations and 24 now-empty rules while retaining every remaining override whose necessity is not mechanically provable. Do not infer dead selectors from source absence, remove `!important` heuristically, reorder selectors, introduce cascade layers, or replace historical literals with design tokens. Those operations can change specificity, compatibility, or source-contract semantics and require a concrete feature-driven reason. CSS source contracts continue to inspect the ordered runtime boundary through `frontend_styles_source()`.

## 10.3.6 — ordered CSS ownership

The backend remains frozen at 10.2.27 and the 10.3.5 JavaScript ownership map is unchanged. 10.3.6 decomposes the 3,445-line `static/styles.css` monolith into explicit CSS owners under `static/css/` without changing source-order semantics: each physical file is a contiguous slice of the previous stylesheet and `static/index.html` loads those slices in the original cascade order. `static/styles.css` now owns only the global foundation slice. Because CSS order is runtime behavior, tests inspect the complete ordered boundary through `frontend_styles_source()` / `frontend_styles_source_from_styles_path()` rather than assuming one physical stylesheet. This release intentionally does not deduplicate selectors, normalize specificity, regroup historical override layers, or redesign presentation; those are separate cleanup decisions after the ownership checkpoint is green. See `static/css/README.md` for the canonical order and ownership rules.

## 10.3.5 — frontend ownership cleanup

The backend remains frozen at 10.2.27 and no schema, persistence format, route, or intended product behavior changes. The completed 10.3.4 JavaScript extraction is refined so ownership matches responsibilities rather than merely filenames: shared typography primitives live in `core/typography.js`; `core/ui.js` now contains shared UI infrastructure only; Manuscript bookmarks and view preferences live with Manuscript; Reviewer Mode view preferences live with Reviews; and Map ruler/text preferences live with Maps. Reviews and Projects are package-owned instead of single-file domains. Manuscript rendering no longer owns browser Undo/Redo, workspace presentation, or save lifecycle; those responsibilities live in `history.js`, `workspace.js`, and `persistence.js`. `static/app.js` remains startup-only. Classic-script execution remains build-step-free and the complete browser source boundary is still the ordered scripts declared by `static/index.html`. CSS remains intentionally monolithic until this ownership checkpoint is verified.

## 10.3.4 — JavaScript ownership migration complete

The backend remains frozen at 10.2.27. The former `static/app.js` feature remainder has been fully decomposed into explicit browser owners while preserving classic-script execution order. Split View lives at `features/split_view.js`; Manuscript import orchestration at `features/manuscript/imports.js`; Checkpoints at `features/checkpoints/workspace.js`; History/Cuts durability UI at `features/durability/history.js`; Notes at `features/notes/comments.js`; and Quick Open at `core/quick_open.js`. `static/app.js` is now a 13-line startup shim only and must not accept feature implementations. The complete browser source boundary remains the ordered scripts declared by `static/index.html`. CSS remains the next frontend architecture target after this JavaScript checkpoint is green.

## 10.3.3 — Maps and Metro browser ownership

The backend remains frozen at 10.2.27. Maps browser ownership now lives under `static/js/features/maps/`: `workspace.js` owns Map navigation, editing, rendering, inspectors, and persisted viewport/UI state; `metro.js` owns the derived Metro View poster model and renderer. Ordered classic-script loading preserves the historical runtime sequence. `static/app.js` remains transitional but now begins at Split View and must continue shrinking rather than regaining extracted behavior.

## 10.3.2 — Tasks/linking/Canvas browser ownership

The frontend remains build-step-free and loads classic scripts in dependency order. Tasks and universal linking now have dedicated feature owners, while Canvas is split into `features/canvas/hierarchy.js`, `nodes.js`, and `connectors.js`. `static/app.js` is a transitional remainder only and begins at Maps. The runtime-source boundary is the concatenation of scripts declared by `static/index.html`; physical ownership moves must preserve that ordering and must not create compatibility copies in `app.js`. The backend remains frozen at 10.2.27.

## 10.3.1 — frontend boot/project/Manuscript ownership

The backend remains frozen at 10.2.27. Frontend runtime ownership now separates crash recovery (`core/recovery.js`), application lease/bootstrap/refresh (`core/boot.js`), Project Center/project settings and backup/recovery shell (`features/projects.js`), and the Manuscript browser domain (`features/manuscript/`). The Manuscript package owns navigation, rendering, editing/working-session browser Undo, Scene actions, annotation/comment UI, and inline Find/Replace in historical execution order. `static/app.js` is only the unextracted feature remainder and must not regain implementations that already have an owner. Source-contract tests read the complete browser runtime through `frontend_source()`.

## 10.3.0 — frontend ownership phase

The backend architecture remains frozen at 10.2.27. Frontend JavaScript now has an explicit runtime ownership tree under `static/js/`. Ordered classic scripts preserve Breadmaker's build-step-free/offline development model while allowing cohesive domains to leave the former `static/app.js` monolith. `app.js` is a transitional remainder, not a compatibility facade or canonical ownership surface. New frontend behavior should live with its owning `core/` or `features/` module. Historical source-contract tests use the complete runtime frontend boundary.

## 10.2.27 — backend architecture freeze

The backend ownership refactor is complete. Every HTTP feature domain now lives under `api/<domain>/`; the remaining formerly-flat Annotations, Comments, Find/Replace, and Icon Markers domains use a deliberately small `routes.py` + `contracts.py` layout with a composition-only package boundary. The `src/api/` root contains only `__init__.py` and the finite shared request primitives in `shared_contracts.py`. Cross-domain calls target the narrow owner (for example `annotations.routes`) instead of a package namespace or compatibility facade.

Project JSON inline-markup escape sentinels are owned by `formats.project_json.markup`, beside the parser that consumes them; validation no longer owns unrelated formatting state. Backend architecture is now frozen for the frontend-refactor phase: do not introduce flat feature modules, compatibility facades, service locators, or further file splitting unless a concrete new responsibility requires it.

## 10.2.26 — final ownership cleanup

Four remaining mixed/global boundaries are removed without compatibility facades. `api.workspace_tools` is now a composition-only package with references, bookmark, UI-state, and document owners. `formats.project_json` separates validation, markup conversion, deterministic layout, import, and export. `workspace.projects` composes recovery/registry, package-transfer, and trash service owners. The former `backend.contracts` registry is deleted: request payloads live beside their owning domains, with only `api.shared_contracts` retaining the common finite-number request base and generic reorder payload.

## Import/Export and Workspace Backup ownership (10.2.25)

`api.import_export` is composition-only. DOCX parsing, bounded upload/archive validation, DOCX import workflows, DOCX export, PDF export, and generic asset upload each have a canonical owner under `api/import_export/`. Cross-feature consumers target `import_export.uploads` directly; the retired flat `api/import_export.py` module must not return.

`workspace.backups` is now a service package composed onto the single `Workspace` object. Destination configuration, immutable publication authority/status, backup generation/retry state, local publication/scrub bookkeeping, automatic catch-up, and destination inventory/materialization live in focused mixin owners. The package boundary composes those responsibilities only; the retired 920-line `workspace/backups.py` implementation must not return.

## Checkpoints domain ownership (10.2.24)

The former mixed `api/checkpoints.py` module is retired. Checkpoint HTTP/domain ownership now lives under composition-only `api/checkpoints/`: `capture` owns snapshot capture/encoding/insertion, `lineage` owns Chapter identity and public metadata, `comparison` owns read-only version diffing, `restore_support` owns annotation/comment/Marker/Scene restore support, `restore` owns destructive Chapter/Book manuscript restoration, `lifecycle` owns Checkpoint CRUD, and `apply` owns preflight/conflict reconciliation/safety-History/apply. The package boundary does not re-export implementation helpers.

**Restore rule:** capture/comparison code must not own destructive manuscript restoration. Cross-domain callers target the narrow canonical Checkpoint owner through `feature_call`, and old `feature_call("checkpoints", ...)` helper edges are forbidden.

## Project Management HTTP ownership (10.2.23)

The former mixed `backend/project_routes.py` platform router is retired. Profile/workspace/project administration now lives under composition-only `api/project_management/`, split into profiles, workspace identity/state, project metadata, destructive actions, project lifecycle, members, native package transfer, public JSON transfer, project assets, and project health. `backend.core` registers the package in the same historical platform route position before the request pipeline; implementation callers target the owning submodule directly and no compatibility facade remains.

## Durability HTTP domain decomposition (10.2.22)

The former `src/api/history_backups.py` mixed HTTP/domain module is removed. `api.durability` is composition-only and preserves the historical route order while explicit owners now separate Cuts, immutable History, structural Undo, edit baselines, Recovery/trash restoration, Backup Destination configuration/providers, Recovery-backup scheduling, destination backup inventory/restore, and verified backup/integrity operations. Cross-feature dependencies target `durability.cuts`, `durability.history`, `durability.backups`, or `durability.backup_destinations` directly. No compatibility facade remains and no schema, persistence format, security policy, HTTP contract, or frontend behavior is intentionally changed.

## Maps and Tasks domain decomposition (10.2.21)

The former flat `src/api/maps.py` and `src/api/tasks.py` modules are removed. `api.maps` now composes explicit owners for manuscript chronology/anchors, Map lifecycle, Lines, Stops/Spans, Junctions, layout, and Populate/source synchronization. Cross-domain manuscript-removal hooks target `maps.anchors` directly; `api.maps.__init__` does not re-export implementation helpers. `api.tasks` is intentionally smaller: Task CRUD, milestone behavior, and duplication each have a focused owner while `api.tasks.__init__` only preserves router order. The 24 Map routes and 10 Task routes preserve their 10.2.20 method/path/handler registration order. No schema, persistence format, security policy, or frontend behavior is intentionally changed.

## Release-contract synchronization (10.2.20)

The Canvas domain remains package-owned as established in 10.2.19. Historical architecture contracts must recognize `src/api/canvas/` as the canonical owner and must not require the removed `src/api/canvas.py` monolith.

## Canvas domain decomposition (10.2.19)

The former `src/api/canvas.py` monolith is removed. `api.canvas` is now a router-composition package with explicit owners for Populate/templates (`populate.py`), developmental synchronization/snapshots (`developmental.py`), Canvas create/read (`registry.py`), Structure Versions/layout/promotion (`versions.py`), Canvas metadata/order/delete (`lifecycle.py`), Cards (`nodes.py`), Connectors (`edges.py` + `edge_styles.py`), and Developmental preflight/apply (`structure.py`). Cross-feature callers target `canvas.developmental` or `canvas.populate` directly; `api.canvas.__init__` does not re-export implementation helpers. The 27 Canvas HTTP routes preserve their previous registration order and behavior.

## Manuscript domain decomposition (10.2.18)

The former `src/api/manuscript.py` monolith is removed. `api.manuscript` is now a router-composition package with explicit owners for navigation/bootstrap projections (`navigation.py`), document/basic Chapter metadata routes (`document.py`), image-block behavior (`images.py`), bookmark/annotation/range repair (`anchors.py`), paragraph-to-Chapter splitting (`chapter_structure.py`), Chapter persistence/structural mutation (`chapters.py`), and Scene ownership/range/movement (`scenes.py`). Cross-feature callers target those owners directly through named `feature_call()` edges; `api.manuscript.__init__` does not re-export implementation symbols. The 24 Manuscript HTTP routes preserve their previous registration order and behavior.

## Reviews domain decomposition (10.2.17)

The former `src/api/reviews.py` monolith is removed. `api.reviews` is a router-composition package whose focused owners cover shared domain primitives, snapshots, presets, portable packages, reader sessions, questionnaires, feedback/suggestions, participants, markers, and lifecycle. Cross-feature callers target those owners directly; no compatibility `reviews.py` facade remains.

## Internal compatibility cleanup (10.2.15)

Historical verification contracts follow the same pre-alpha ownership rule as production code: when an internal owner moves, tests move to that owner or to a boundary-level source helper. They do not require deleted service-locator files or namespace bridges to remain present. `storage/core.py` stays deleted, storage-wide historical source checks use the decomposed storage boundary, and `app.py` remains an explicit FastAPI composition root rather than a `sys.modules` alias.

## Storage ownership decomposition (10.2.15)

Canonical persistence has no service-locator facade. The temporary `storage/core.py` compatibility boundary was deleted after callers and tests moved to focused owners: `storage.context`, `coordination`, `database`, `mutations`, `history`, `undo`, `assets`, `invariants`, `integrity`, `recovery`, `values`, and `schema_contracts`. Cross-owner runtime calls are module-qualified so the declared owner remains authoritative for mutable state, failure injection, and diagnostics. The SQLite schema and durability contracts are unchanged. See `src/storage/README.md` for the ownership table.

## Workspace filesystem platform-owner correction (10.2.12)

Removable-volume discovery is owned by `workspace.filesystem`. Consumers such as `workspace.backups` resolve both `_volume_metadata` and `_mounted_volume_roots` through that owner at call time instead of retaining copied bindings. Historical tests patch the same canonical owner. This keeps platform-specific overrides, tests, and production behavior aligned on one namespace while the public `workspace` package boundary remains implementation-free.

## Workspace canonical-owner lookup correction (10.2.11)

The 10.2.10 Workspace package boundary remains frozen: `workspace/__init__.py` contains only stable public exports and composition. Internal source contracts target their canonical modules. Cross-owner runtime dependencies that are intentionally patchable/platform-specific (for example volume metadata) are resolved through the owning module rather than copied into consumer module globals, so tests and runtime overrides observe the same authoritative implementation.

## Workspace package ownership (10.2.10)

`workspace/__init__.py` is now a lightweight package boundary rather than the implementation of the entire installation/project repository. The stable `Workspace` object remains one composition surface, while implementation ownership is explicit: profiles (`workspace.profiles`), non-portable memberships/registry reconciliation (`workspace.memberships`), project/package lifecycle (`workspace.projects`), Remote History (`workspace.remote_history`), credentials/OAuth (`workspace.credentials`), backup destination/publication orchestration (`workspace.backups`), privacy erasure (`workspace.erasure`), providers (`workspace.providers`), durable filesystem helpers (`workspace.filesystem`), records/errors (`workspace.models`), and package/schema constants (`workspace.constants`). `workspace.service` composes these owners and retains only installation paths/locks, registry initialization, and manifest durability.

**Ownership rule:** package `__init__` files are stable boundaries, not compatibility dumping grounds. Production code that needs a workspace implementation helper imports its canonical owner directly. Tests that patch internals patch the module where the implementation resolves the name. The single `Workspace` instance remains the orchestration object, so this source decomposition does not duplicate mutable workspace state.

## Canonical outer-server dependencies and History observer (10.2.9)

`src/server.py` remains the package-qualified outer ASGI boundary, but it is not a platform service locator client. It delegates HTTP application dispatch only through the composed `app` module; hosted request ceilings come from `backend.environment` + `security.hosted_guardrails`, and project-free runtime bootstrap metadata comes from `backend.request_pipeline`. `backend.core` does not re-export those dependencies.

Canonical Current commits notify immutable local History through `storage.mutations.register_canonical_commit_listener(backend.local_history._canonical_commit_history_observer)`. The registration lives beside the observer owner, while the observer itself remains non-blocking and only enqueues work when the local History worker is active. Current durability therefore remains independent from History publication.

## Thin backend composition boundary (10.2.8)

`backend.core` is now composition-only. The previous lifecycle/middleware/platform-HTTP implementation has been split by responsibility: `backend.local_history`, `backend.lifecycle`, `backend.http_boundary`, `backend.client_leases`, `backend.danger_challenges`, `backend.request_pipeline`, and `backend.runtime_routes`; profile/workspace/project-administration HTTP ownership lives under `api.project_management`. `backend.core` creates the FastAPI application and installs these owners in deterministic registration order. It does not re-export implementation helpers for tests or tooling.

**Ownership rule:** implementation state and behavior live in the focused owner, never in `backend.core`. Feature modules continue to have zero imports from `backend.core`, and tests that need process internals import/patch the owning module directly.

## Canonical-owner verification cleanup (10.2.7)

10.2.6 completed the feature-side removal of `backend.core` dependencies. Verification then exposed historical tests that still reached moved contracts and process services through the old composition namespace. 10.2.7 updates those tests to target the same canonical owners as production code and intentionally keeps the removed `backend.core` aliases absent.

**Regression rule:** refactor-sensitive tests must import or monkeypatch the canonical owning module. A test may not require `backend.core`/`app` to re-export a feature contract or feature-owned service merely to preserve an old source shape.

## Stateful backend ownership (10.2.6)

Feature modules now have **zero imports from `backend.core`**. Stable platform concerns remain under `backend.content`, `backend.environment`, `backend.configuration`, `backend.data_access`, and `backend.request_context`; HTTP payload contracts now live beside their owning API domains rather than in a global backend registry. The last stateful edges now have explicit owners: standalone/temporary Portable Review state in `backend.portable_review_runtime`, Recovery and backup-coordinator state in `backend.recovery_runtime`, Remote History coordination and explicit History landmarks in `backend.history_runtime`, and project switching in `backend.project_activation`. `src/api/dependency_manifest.json` records these owner-qualified dependencies.

Reassignable process state has one canonical owning module. Consumers call that owner or an accessor/service on that owner; they do not copy scalars into feature modules or regain access through a `backend.core` compatibility facade. `backend.core` is now a thin composition boundary; lifecycle, middleware, local History publication, and platform HTTP behavior live in focused backend owners.

## Explicit platform ownership (10.2.5)

10.2.5 moved stable contracts, content/configuration, data-access, environment, and request-context concerns out of the historical `backend.core` service-locator boundary. 10.2.6 completes that feature-side dependency cleanup by extracting the remaining stateful lifecycle/activation edges.

## Canonical package imports (10.2.4)

Backend ownership is explicit at the import boundary. Persistence consumers import the focused `storage.*` owner for the behavior they use; authentication consumers import `auth.sessions`/`auth.identity`; security consumers import `security.content_security`/`security.hosted_guardrails`; portable Review consumers import `formats.breadreview`. Package `__init__` files stay lightweight and must not become compatibility dumping grounds.

## Backend package ownership (10.2.3)

`src/` now contains only process entrypoints (`__init__.py`, `app.py`, and `server.py`). Former loose runtime modules have explicit owners: `auth/` for identity/session primitives, `security/` for content/hosted protections, `formats/` for portable serialization, `storage/` for canonical persistence/schema, `workspace/` for installation/project orchestration, and `backend/` for platform coordination/diagnostics. `storage` and `workspace` retain their historical import names as packages, so the layout cleanup does not create a second implementation or compatibility shim. New backend modules must be placed under an owning package rather than added to the `src/` root.

## Explicit backend dependency graph (10.2.x)

The 10.1 compatibility bridge has been removed. Feature modules no longer receive the composition-root namespace or export their implementations back into every other domain. Each feature imports platform symbols from canonical owning modules and there is no stateful allowlist remaining: feature code has zero imports from `backend.core`. The mature cyclic helper graph is represented as named lazy function edges through `feature_call(owner, symbol)`. Cross-feature mutable state is forbidden. `src/api/dependency_manifest.json` and its generator make this dependency graph inspectable by humans and coding agents. Route order and HTTP contracts remain unchanged.

`src/app.py` and `backend.core` are both composition-only. Platform behavior lives in focused backend/runtime/storage services; neither composition module should accumulate feature or process implementation.

## Reassignable state across the API bridge (10.1.2)

During the transitional 10.1 bridge, stable functions and mutable objects could be shared by reference, but feature modules were forbidden from capturing reassignable composition-root scalars. Values such as Recovery mode are resolved through composition-root accessors so startup/reset changes remain visible after router installation. Feature-owned helpers remain canonical in their `src/api/<domain>.py` module; 10.2 removed the broad aliases that the old bridge exported.

## Cross-domain compatibility bridge (10.1.1)

The initial 10.1 extraction revealed that mature Breadmaker domains have a small cyclic helper graph (for example Manuscript bootstrap reads Review summaries while Reviews reuse Manuscript anchoring helpers). The transitional `src/api/_bridge.py` therefore resolves feature modules in two phases: import all modules against the composition root, collect every explicit `__core_exports__` symbol, then re-bind the completed namespace into every feature module before router registration. New code should prefer explicit service/module dependencies; the bridge exists only to preserve behavior while the extracted domains are progressively made independent.

## Domain API architecture (10.1.0)

`src/app.py` is the platform/composition root: lifecycle, middleware, request identity/project binding, shared process coordinators, and application assembly. Product feature endpoints live under `src/api/` by domain. The retired 10.1 `_bridge.py` made the original extraction behavior-preserving; 10.2 replaces it with explicit dependency declarations. New feature routes belong in their owning domain module and should prefer explicit lower-level runtime/storage services. `AGENTS.md`, `src/api/CODEMAP.md`, `src/api/route_manifest.json`, and `docs/CODE_INDEX.json` are the navigation surfaces for humans and coding agents.

## Repository boundary (10.0.0)

10.0.0 separates the product source tree from the managed-service operations tree. `breadmaker` owns application behavior and its local/hosted runtime contracts. `breadmaker-cloud` owns the concrete `breadmaker.studio` infrastructure and deployment automation. The split is an operational/access boundary, not a secrecy dependency. No 9.6.15 runtime/security behavior is intentionally changed by this release.

## Verification isolation cleanup (9.6.15)

9.6.15 contains no Recovery scheduler runtime change. The remaining reconcile failure was caused by a test that directly drove the process-global worker while inheriting pending scheduler objects from earlier tests. The integration contract now resets rebuildable coordinator memory before constructing its single-project scenario. Historical patch contracts are forward-compatible, and the localhost runtime-fallback diagnostic reports the current frontend release.

## Verification contract stabilization (9.6.14)

No runtime architecture changes. Release verification now checks semantic boundaries against the current implementation shape rather than relying on stale raw-source positions or an obsolete inline project-import identity expression.

## Bounded hosted authorization/privacy closure (9.6.13)

Hosted collaboration distinguishes content editing from project administration. Editors retain manuscript/review/task/canvas/map editing rights, while backup destination administration, Recovery backup administration, History restore/retention/sync/repair/GC, and manual backup controls require project.manage (Owner). Detached Portable Review state follows opaque-session lifecycle: explicit logout cleans immediately and expired sessions are pruned on subsequent authenticated traffic. Public hosted project records omit owner_user_id; project-local member_id/role remain the collaboration identity.

## Coordination, local secrets, and destructive intent (9.6.12)

Breadmaker's single-writer lease is a coordination primitive rather than authorization. An uncontended browser may renew its 90-second lease forever. Once a foreign client is waiting, however, continuous ownership is bounded; after the configured tenure and wait grace the waiter may acquire the lease. This preserves the current no-simultaneous-editing model while removing indefinite member-driven starvation.

Destructive confirmation challenges are ephemeral, single-use, and bound to `(actor, action, target)`. Project deletion binds to the concrete project id; local Delete All and managed-backup erasure bind to the resolved Breadmaker home. Permission checks still execute independently at the destructive endpoint.

Installation-local backup credentials are stored as AES-GCM sealed records. The per-installation 256-bit key is owner-only, lives outside project packages/exports, and authenticates each credential id as associated data. This prevents copied `backup_credentials.json` files, support bundles, or accidental file disclosure from revealing remote-backup secrets as plaintext. It is not intended to defeat compromise of the logged-in OS account, which necessarily has enough authority to run Breadmaker and use its configured backups.

Hosted 9.6 remains intentionally single-worker because sessions, editor leases, rate buckets, and coordinator state are process-local. The runtime now enforces that constraint using an exclusive workspace lock in addition to advertising it as metadata. Legacy `.doc` conversion is prohibited in hosted mode, avoiding execution of a large native parser against untrusted public uploads; the hosted image runs as a non-root service account.

Authenticated member listings expose project-local membership identity to ordinary members and reserve globally stable account ids for membership managers. Browser-local Review-panel state is keyed by both user and project.

## Hosted trust, resource, and browser boundaries (9.6.11)

Public hosted Breadmaker treats imported browser-renderable content and shared compute/storage as explicit security boundaries. Persisted image references are canonical same-origin `/api/assets/<name>` values; temporary BreadReview images are canonical `/api/portable-review/assets/<name>` values. Package validation rejects external, protocol-relative, data-URL, query-bearing, nested, or otherwise noncanonical image references before imported state can render. The frontend applies an independent display allowlist, while CSP limits image fetches to `self`, `data:`, and `blob:` as defense in depth.

`hosted_dev` developer identity remains useful only for development/staging. When `BREADMAKER_ENV=production`, developer authentication is rejected before Workspace initialization regardless of `BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED`. Production deployments must use an authenticated account-session adapter.

Hosted resource controls are deliberately absent from the local desktop runtime. Hosted mode applies configurable project-count, owner-storage, project-storage, upload, expanded-archive, and request-body ceilings plus process-local per-identity write/heavy-operation rate buckets. Trash belongs to the owner's storage accounting. Because 9.6 still advertises `single_worker_required`, the limiter is process-local by design; it must move to the same shared coordination backend as sessions before multiple replicas/workers are allowed. `src.server:app` owns the streamed body ceiling so chunked requests cannot bypass limits by omitting `Content-Length`.

The application shell is returned with a restrictive Content Security Policy and frame/MIME/referrer/permissions hardening. Hosted HTTPS responses also receive HSTS. CSP is defense in depth rather than a substitute for import sanitization or authorization.

## Startup projection and first-paint boundary (9.6.10)

Application bootstrap is navigation metadata, not an eager editing snapshot. It must not compute Chapter revision hashes over the entire manuscript before first paint. One lightweight ordered manuscript projection now supplies Scene first/last/count metadata and unassigned Scene gaps; prose text is transferred only for unassigned paragraph rows whose word count is actually needed. Exact Chapter revisions remain authoritative but are loaded by Chapter/Book/Scene hydration, or lazily for the exceptional crash-draft chapters that require a revision comparison.

The browser overlaps independent bootstrap work rather than serializing it: locale and runtime capability discovery start together; profile, detached-Review status, and workspace selection start together; once project identity is known, lease acquisition overlaps read-only bootstrap, batched member UI state, and local crash-journal lookup. The editor still waits for the lease and routed workspace hydration before declaring boot ready, so this is latency removal rather than a consistency relaxation.

Full Text caching is navigation-only. Any explicit server-side structural manuscript reload invalidates the active Tab's book cache before rehydration, ensuring image insert/copy/move operations cannot render stale cached book state. Background immutable History remains writer-idle and asynchronous; tests may shorten that debounce in isolation, but production must not move full History capture back into the interactive path.

## Package-qualified launcher boundary (9.6.9)

The desktop process must never resolve the Breadmaker backend through an ambiguous top-level module name. The launcher targets `src.server:app`; the outer server imports its sibling composition root with `from . import app as core`, while importing bootstrap metadata and hosted guardrails from their canonical backend/security owners. This guarantees that the delegated FastAPI application refers to the packaged Breadmaker source tree without turning the composition module back into a service locator.

`/api/runtime` remains owned by the outer ASGI boundary. It calls the canonical `_runtime_bootstrap_payload()` helper and deliberately does not infer capabilities from arbitrary module attributes. The browser's local fallback is a resilience path only: it accepts bootstrap 404/500 on localhost, then requires `/api/health` to attest both the Breadmaker application identity and `local` runtime before using the compiled local capability set. Hosted/account modes never use that fallback.

## Outer runtime-bootstrap boundary (9.6.8)

The downloadable launcher starts Uvicorn with `server:app`, a minimal ASGI wrapper that owns `/api/runtime` outside the main FastAPI application. Runtime discovery therefore has two independent implementation boundaries: the outer launcher-facing ASGI seam and the canonical route/middleware in `app.py`. A localhost browser may recover from a missing `/api/runtime` only after `/api/health` attests that the process is Breadmaker Editor in `local` runtime mode; it then uses the exact fixed LocalCapabilityProvider contract. Hosted or account-authenticated runtimes never use this fallback. This design makes a stale/mixed desktop extraction diagnosable and survivable without allowing runtime-capability guessing across a security boundary.

## Project-free bootstrap boundary (9.6.7)

The browser's first capability request is `/api/runtime`. It is a global runtime-discovery endpoint, not a project operation. The HTTP middleware therefore answers GET/HEAD `/api/runtime` before session actor lookup, local-profile resolution, project selection, membership checks, Recovery context, or editor leases. This keeps startup diagnostic/recovery surfaces reachable even when Current cannot be opened and prevents installation-local state from converting runtime discovery into a misleading 404. The FastAPI route delegates to the same pure payload helper for direct dispatch parity.

## Interactive persistence hot paths (9.6.6)

Breadmaker Current remains transactional and durable, but read models and derived-history work no longer participate in the writer's latency path. SQLite WAL mode is persistent configuration established during project initialization. Normal request connections do not renegotiate journal mode. Latency-sensitive GET surfaces (`bootstrap`, Chapter/Book, Canvas detail, Map detail/catalog, UI-state reads) use read-only WAL connections and therefore do not acquire the mutation coordinator's `BEGIN IMMEDIATE` reservation.

Session Undo stays row-delta based, but Canvas/Map scopes can now carry the concrete domain object ID. Populate therefore snapshots only rows owned by that Canvas/Map rather than every Canvas/Map in the project. Canvas creation uses a bounded Canvas-domain scope because its new database ID does not exist at the before-snapshot boundary.

Immutable History remains downstream of canonical Current. Canvas Create/Populate do not perform a synchronous full project freeze after their canonical transaction. The canonical History observer collapses changes during a 90-second active-edit quiet window and publishes the newest generation after the writer becomes idle; shutdown still drains the newest pending generation immediately. Recovery remains a separate authority.

Story Map manuscript projection is a derived read model cached by manuscript-specific `(count, max(updated_at))` signatures rather than whole-project generation, so Map-only edits cannot invalidate the expensive prose projection. Full Text similarly derives Chapter revisions from its one already-loaded block stream instead of re-querying each Chapter.

## Performance recovery without weakening hosted security (9.6.5)

Breadmaker startup now separates **lightweight navigation state** from **heavy workspace payloads**. `/api/ui-state?keys=…` reads a bounded set of member-scoped preferences in one project transaction. The browser applies those preferences before exposing workspace bodies, then hydrates only the routed heavy surface: Manuscript content for Manuscript, the remembered Canvas for Canvas, or the remembered Story Map for Maps. Inactive heavy workspaces remain cold until visited. The top bar remains visible while workspace bodies are withheld, eliminating the previous default-state flash without replacing it with an application-wide loading screen.

Canvas and Map workspace transitions treat outgoing viewport persistence as best-effort write-behind rather than a navigation dependency. Map detail payloads have one cache plus one in-flight request registry; expanded off-screen map details are scheduled during idle time. These changes remove redundant network/SQLite work from navigation while keeping explicit reload paths for mutations that invalidate cached data.

Session Undo remains fail-safe by default, but operations with a proven mutation boundary may opt into an explicit scope. Canvas Populate owns `canvas_nodes`/`canvas_edges`; Map Populate owns `map_lines`, `map_items`, `map_junctions`, and `references_map`. Those actions therefore journal only their domain before/after state instead of every mutable project table. All unclassified or cross-domain structural actions retain the full snapshot, and the delta/conflict/rollback engine is unchanged.

The imported-package XSS boundary remains mandatory. Normal editing still canonicalizes HTML with the existing BeautifulSoup sanitizer. At package/Recovery verification, however, Breadmaker now uses a streaming `HTMLParser` capability validator: only the inert tag set is accepted, prose attributes are forbidden, Cut image paths must remain project assets, figure metadata is bounded, declarations/comments/unknown tags are rejected, and rendered prose text must still equal stored `text_content`. This preserves the security decision while avoiding a parser DOM allocation for every paragraph during background package verification.

Release verification keeps the hard ten-second per-test watchdog. The intentionally large 10,000-paragraph scale contract runs after ordinary parallel core jobs in a single-worker no-contention lane, so the watchdog measures Breadmaker rather than CPU contention from seven unrelated pytest processes.

## Critical-security corrective pass (9.6.4)

The 9.6.3 boundaries remain authoritative: detached Review state is server-session scoped, `import-current` binds the authenticated actor/project before storage access, and package hashes never imply HTML trust. 9.6.4 corrected compatibility and publication-order regressions discovered when 9.x contracts were restored to the release verifier; it did not relax the tenant or XSS boundaries.

## Critical hosted package/isolation hardening (9.6.3)

Portable Review mode now has two deliberately separate ownership models. The standalone local `BREADMAKER_PORTABLE_REVIEW` process may own one package, but authenticated hosted runtimes are forbidden from starting in that mode. Drag-and-drop temporary packages are instead keyed by the opaque server `session_id`; their path, temporary root, filename, dirty bit, and unlocked passphrase are inaccessible from every other session. A session logout removes its detached package before revoking the session.

Portable Review endpoints remain project-context-free while inspecting a detached file. Importing returned feedback is different: Breadmaker resolves the current project only through the authenticated account, compares it to the package `project_id`, then opens an explicit `runtime.context.bind(project_id, user_id=...)` and `review.process` authorization boundary before any SQLite transaction. Detached package handling therefore cannot inherit the local shell's process-global project root.

Package integrity and browser rendering trust are now separate checks. `.breadmaker` verification performs an independent capability check over every persisted `innerHTML` surface (Manuscript, Review snapshot, Cut): active/unknown tags, attributes that could execute or navigate, unsafe image sources, and HTML/plain-text disagreement are rejected. Harmless historical paragraph wrappers are accepted so current-schema History/Recovery packages do not become unreadable merely because their serializer differs from today's canonical editor output. `.breadreview` validation applies the same frozen-manuscript boundary before Reader Mode sees a manifest. SHA-256 package/payload hashes and the canonical state hash still prove internal consistency, but an attacker who recomputes those values does not gain permission to introduce executable HTML.

## Account-session isolation hardening (9.6.1)

9.6.1 removes the remaining local-shell assumptions from authenticated hosted sessions. Account routing derives a project only from the request context or the authenticated user's memberships; the process-global selected project is never an account fallback. Background History and Recovery coordinators similarly operate only on explicit pending project work under account sessions.

Installation-local membership repair is now policy-dependent. Local Breadmaker and the single trusted `hosted_dev` developer identity may reclaim a copied `local.json` sidecar, but authenticated `test`/`session` runtimes use `fail_closed`: invalid membership metadata is omitted from normal project discovery and left byte-for-byte available for deliberate operator recovery. No account is silently granted ownership.

Hosted identity travels only in the opaque HttpOnly session cookie. The browser does not emit `X-Breadmaker-User` or `user_id` in account-session mode, authenticated API responses and isolation failures are `private, no-store`, and auth session/logout endpoints are independent from project context. Browser crash-journal rows are scoped by user + project + chapter, while persistent project UI-state keys are namespaced by the project-scoped `member_…` identity rather than portable/account `usr_…` IDs.

Test identities are bound through a test-only fixture-ID registry instead of human display names. This keeps deterministic automation without creating an arbitrary impersonation surface. Sessions remain in-memory/single-worker and Auth0/OIDC remains the next authentication adapter; 9.6.1 does not claim production readiness.

## Authentication/session boundary (9.6.0)

Breadmaker authentication is now separated into two steps: an authentication adapter proves an external/local test identity, then `SessionService.issue()` creates an opaque Breadmaker server session for an existing `usr_…` user. Normal application APIs accept only the session cookie in hosted session mode; browser-supplied `X-Breadmaker-User` values are ignored as identity authority. The cookie contains a cryptographically random 256-bit token, while the server retains only a SHA-256 digest plus user/auth-method/expiry metadata.

`BREADMAKER_AUTH_MODE=test` is a CI-only authentication adapter. The route is registered only when the hosted runtime is built in test mode, which itself requires `BREADMAKER_ENV=test` and a dedicated secret of at least 32 characters. The secret can bootstrap only named fixtures and cannot be reused as a bearer credential against normal Breadmaker APIs. `BREADMAKER_AUTH_MODE=session` enables the same authenticated session boundary without providing a login adapter; the upcoming Auth0/OIDC callback will enter through this path rather than changing project/domain authorization.

Sessions are intentionally in-memory and single-worker in 9.6 because the hosted runtime remains a development target. Production deployment will require the external IdP callback plus a shared/durable session backend (or equivalent single-session authority) before horizontal workers are allowed. Local Breadmaker still uses local profiles and a `NullSessionService`; no account is required for the free local runtime.

Installation-wide Recovery candidates, Export All, and Delete All are local-administration concepts and are not exposed to hosted account sessions. Hosted users see their membership-scoped projects/Trash and use project-level export/delete until account-scoped recovery/export/erasure is implemented.

## Hosted development runtime (9.5.0)

9.5 adds the first non-local runtime without forking Breadmaker. `RuntimeServices` can now be built as `local` or `hosted_dev`. Hosted development keeps the proven Schema-41 SQLite-per-project domain store, but places the workspace beneath a server-owned durable `BREADMAKER_HOME`; native filesystem/removable-backup capabilities are not exposed to the browser. The hosted identity provider is deliberately fixed server-side and ignores `X-Breadmaker-User`/`user_id` as authority. Because authentication is not implemented yet, hosted mode fails closed unless `BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED=1` is explicitly supplied. Mutating browser traffic is same-origin restricted.

This is intentionally a persistence/deployment proof, not the final storage topology. PostgreSQL/object-storage adoption should replace adapters or selected repository internals only when doing so improves hosted operation; Manuscript, Canvas, Maps, Reviews, and the local free runtime must not fork.

## Request-context coordinator hardening (9.4.1)

9.4.1 keeps the 9.4 request/project/actor model unchanged and corrects the lifecycle of its in-memory Recovery coordinators. Per-project scheduler objects are caches of pending process work, not durable project state. Clean inactive schedulers may therefore be discarded; the durable Recovery high-water remains `backups/recovery_state.json` inside each project. A fresh application lifespan rebuilds coordinator state from project authority instead of inheriting retry timers or clean scheduler objects from an earlier in-process lifespan.

This also makes the old History switch safety test reflect the request-scoped architecture: a process-global shell switch no longer invalidates an already-bound request. Safety is proven by ensuring the History-derived Checkpoint is committed only to the request's project and never to the newly selected project.

## Request/session-scoped project runtime (9.4.0)

Breadmaker's local shell still has one selected project for navigation, but that selection is no longer the authority for project data access. API middleware resolves a project identity and local actor once per request and binds a `ProjectRequestContext`. Storage uses a `ContextVar` project binding for database/assets paths, while authorization and Review event attribution reuse the captured actor for the request lifetime.

The local runtime transports context with `X-Breadmaker-Project` and `X-Breadmaker-User` (or query parameters for raw browser resources such as `<img>`). `X-Breadmaker-User` is **not authentication**; local profiles are a convenience/ownership boundary only. A hosted runtime must derive the actor from authenticated server session state and ignore any client claim that attempts to choose an account identity.

Process-global local UI selection remains for desktop ergonomics, startup/recovery management, and profile switching. Canonical request work must not depend on that mutable selection. Project activation/import code that intentionally changes the local shell opens a nested context before writing the newly selected project's content.

Editor leases are project-scoped. Structural Undo entries are already project-bound and activation now invalidates only the activated project's journal entries rather than clearing unrelated project sessions. Recovery scheduling is also maintained per project so a request-scoped mutation receives the same durability behavior as the globally selected book.

The local process also retains an independent OS lock for every project first touched by a request. Browser boot resolves the profile-scoped Workspace and Recovery state before it claims an editor lease, preserving the rule that an unreadable project cannot block access to Recovery/Project Settings.

This release removes the single-active-project backend assumption from normal project requests. Hosted persistence and authentication are still separate future adapters; the current local runtime continues to use Workspace + SQLite + filesystem storage.

## Runtime boundary hardening (9.3.1)

The runtime boundary is now fail-closed at both the browser and API capability edges. The frontend must obtain `/api/runtime` successfully and verifies that Workspace reports the same runtime mode. Native-folder and direct-remote Backup Destination flows are exposed only when the selected runtime advertises those capabilities. Staged project-image uploads are bound to the asset root where the upload began and cannot finalize into a different project after a concurrent project switch.

One hosted prerequisite remains intentionally unresolved in 9.3.x: the manuscript/database domain still uses `storage.configure_project(...)` and one process-global active project/lease. That is correct for the local executable, but it is not a safe multi-user server context. A hosted runtime must introduce request/session-scoped project binding before Breadmaker Studio serves multiple users from one process. The provider interfaces added in 9.3 remain useful seams, but they do not by themselves make the current process model multi-tenant.

## Runtime infrastructure boundary (9.3.0)

Breadmaker has one application/domain layer and an explicit runtime infrastructure boundary. `src/runtime/contracts.py` defines provider-shaped contracts for identity, authorization, project containers, project assets, History infrastructure, backup infrastructure, and runtime capabilities. `src/runtime/local.py` is the only implementation shipped in 9.3.0 and delegates to the existing proven Workspace/SQLite/filesystem systems.

The boundary is intentionally not a second storage engine and does not introduce a cloud model prematurely. Manuscript, Canvas, Maps, Reviews, Tasks, project schema, and their SQLite transactions remain unchanged. Instead, host/environment concerns at the API edges no longer need to know that the current implementation is local: profile/project catalog operations go through `runtime.identity` / `runtime.projects`, authorization through `runtime.authorization`, project image materialization through `runtime.assets`, History transport/retention through `runtime.history`, and backup destinations through `runtime.backups`.

The frontend reads `/api/runtime` once at boot and exposes a capability check rather than a build-target check. The local capability set explicitly describes what this runtime supports (offline operation, local profiles, native filesystem, removable/remote backups, portable exports) and what it does not yet support (hosted projects, account authentication, hosted/realtime collaboration). Future hosted adapters must implement the same contracts; feature code should ask for capabilities or services, never branch on a separate Breadmaker product. Unknown `BREADMAKER_RUNTIME` values fail closed. Schema remains 41 and portable project packages remain runtime-neutral.

## Local membership recovery hardening (9.2.1)

`local.json` is installation-local identity metadata, not portable project content and not a project schema. A project folder copied into another Breadmaker home can therefore legitimately carry profile ids that do not exist in the destination installation. Reconciliation treats those ids as foreign local metadata rather than project corruption: it preserves any unambiguous membership rows that still reference profiles in the current installation, and otherwise reclaims the project for a valid local profile. The project database, manifest, Review content, and schema are never rewritten as part of that repair.

## Local membership and actor authorization (9.2.0)

Breadmaker now treats the active local profile as an actor rather than merely a project-list filter. Every local project has installation-only membership metadata in `local.json`: one Owner and zero or more Editors, each with a stable project-scoped `member_…` identity linked to an installation-local `usr_…` profile. The local sidecar remains outside `.breadmaker` packages, so portable project content never acquires a machine-local account identifier. The retained `owner_user_id` field is derived recovery metadata; membership rows are authoritative.

Project visibility and activation are membership-based. Owners and Editors can open and edit shared local projects; only Owners receive project-management permissions such as deletion and membership changes. The `src/identity.py` service is the first authorization seam: it resolves the current actor and maps roles to named permissions without making the UI or domain logic depend on a future authentication provider. Review Participants remain a separate domain and are not silently converted into project members.

Writer-side Review events now capture a portable actor snapshot containing the project-scoped member id, current display name, and role. They deliberately do not persist the installation-local `usr_…` id. Reviewer-authored Review events continue to be attributed through `review_participants`. Project schema remains 41; the membership representation is installation state, not project schema.

## Local profiles and project ownership (9.1.0)

Breadmaker supports multiple installation-local `usr_…` profiles with exactly one active profile. Profiles live under the Breadmaker home, not inside project databases or portable project packages. The avatar menu can create/switch profiles; authentication still does not exist and `Log out` remains intentionally unwired.

Each local project has one local owner profile. Ownership is recorded in a small `local.json` sidecar beside the project manifest/database and cached in the workspace registry. The sidecar is deliberately excluded from `.breadmaker` exports, so moving a project to another installation never carries a machine-local identity. If the reconstructible registry is lost, Breadmaker can recover the profile/project relationship from the sidecar. Project lists, project switching, and Trash are filtered to the active profile.

A profile remembers its most recently opened project. Switching profiles changes the active identity and activates that profile’s last project; a brand-new profile receives a blank project on first activation. This is a local ownership/view boundary, not an authorization boundary. Real authenticated authorization remains future work. Project schema remains 41.

## Local identity boundary (9.0.0)

Breadmaker now has an installation-local user profile, but not authentication. The local profile lives under the Breadmaker home rather than inside any project database. This makes one identity available across projects while keeping `.breadmaker` exports portable and free of installation/account state. The profile owns a stable `usr_…` identity, display name, and optional normalized avatar. Future account authentication can claim or map this local identity without changing project ownership semantics prematurely.

Avatar source files are never retained. The browser decodes the chosen image, resizes its longest edge to at most 512 pixels, re-encodes it as WebP, and sends only that normalized payload to the local server. Without a custom image, the profile name deterministically seeds a mirrored line identicon and a constrained two-color palette with deliberately separated lightness.

The top-right profile control is therefore UI for identity, not evidence of a signed-in session. In 9.0.0 `Log out` is intentionally present but has no authentication behavior. Project schema remains 41 because no project-format field changed. Installation-level Delete All Data removes the profile directory together with the Breadmaker home.

## Manuscript Review working layer (8.20.0)

### Review backend ownership

The Review backend is a package, not a monolithic route file. `api.reviews.__init__` composes routers only; `lifecycle`, `snapshots`, `portable`, `reader`, `questionnaires`, `feedback`, `participants`, `markers`, and `domain` own their respective behavior. Internal callers and tests import those canonical owners directly; Breadmaker does not preserve an obsolete `api/reviews.py` compatibility facade.

Manuscript Reviews is a writer-facing processing layer over the canonical Review domain, not a second Review system. The panel has two states: a compact round list filtered by Open / All / Closed (Open by default, live/recent rounds first), and a selected-round detail view. Detail can filter by reviewer, navigate inline feedback in frozen manuscript order, close/reopen the immutable round through the existing Review lifecycle, open questionnaire/general feedback, create Tasks through the existing Review-feedback linking path, or jump to the full Reviews workspace for administration.

Inline Review overlays are opt-in: they exist only while the Manuscript Reviews panel is open on a selected round. Highlights, Comments, and Suggestions reuse the frozen Review anchors but are painted against Current only when every referenced source block still exists and the exact selected text still matches the frozen selection. Changed/missing passages fail closed and are never heuristically rebound. The panel and manuscript share one selected feedback identity, so navigation and gutter selection stay synchronized. The last selected round, list filter, and reviewer filter are stored as UI state; Review data remains authoritative in the Review domain.

## Manuscript image rows (8.19.12)

Image rows are deliberately constrained layout metadata, not a general page-layout system. Two to four adjacent image blocks may share an `image_row_id`; the existing `image_width` becomes the width of the whole row and must match across all members. Each member keeps its own asset reference, Footer, and Description. Row membership is created/reordered/removed only by image drag/drop and is preserved by durable manuscript-derived artifacts.

## Manuscript image completion parity (8.19.11)

Image occurrence presentation is preserved outside the editor as well as inside it. DOCX/PDF export size is derived from the manuscript text-column percentage rather than source pixel dimensions; Footer remains a separate readable caption measure, and DOCX carries Description as image accessibility metadata. Read-only manuscript renderers share the same delayed Description hover behavior as Current. The manuscript paper itself owns a terminal image drop target so the blank area below the final block represents an explicit append position rather than a dead drop zone.

## Manuscript image editing interactions (8.19.10)

Image insertion anchors retain a live reference to their manuscript block until the pre-insert save completes, so temporary DOM block IDs can be replaced safely before the image API call. Drag/drop uses an explicit visual insertion indicator and supports `at_offset` movement into prose, splitting the target paragraph while preserving the image occurrence as one atomic block. Image size is presentation metadata on the image itself; Footer measure is independent from image width so captions remain readable for small images. Backspace/Delete at either side of an image removes the atomic image occurrence without merging or restyling neighboring prose, while focused image blocks are directly deletable with the same keys.

## Manuscript toolbar tooltip language (8.19.9)

Manuscript toolbar tooltips describe the writer-facing purpose of each tool rather than implementation mechanics such as opening a side panel. Image explains image placement and presentation controls; Reviews describes Review feedback in manuscript context. This matches the established Notes, Markers, Checkpoints, Cuts, and Editor’s Eye tooltip voice.

## Manuscript Split View target isolation (8.19.8)

Manuscript Split View has an independent read-only target context. Choosing another Tab for a Chapter, Scene, or Full Tab comparison never changes `state.activeDocumentId` or navigates the editable manuscript surface. Chapter and Scene targets are filtered by an explicit Split View Tab selector; Full Tab reads `/api/book?tab_id=...` and renders that complete Tab in chapter order. All manuscript split targets reuse one block renderer so prose and first-class image occurrences remain presentation-consistent.

## Manuscript image blocks (8.19.7)

Manuscript images are atomic blocks between prose blocks; Breadmaker does not support inline/floating images or text wrapping. The Image side panel is a contextual inspector rather than a media library. An occurrence stores a shared project asset reference plus `image_width` (5–100% of the manuscript text column), an optional visible Footer, and an optional hidden accessibility Description. Clicking an image selects the whole figure; dragging it reorders the entire image + Footer block. New files can be inserted from the panel, pasted from the clipboard, or dropped onto manuscript text.

Image bytes are stored unchanged and content-addressed (`img_<sha>.<ext>`) so transparent PNG/WebP data is not flattened and identical bytes can be reused. Occurrence metadata is independent, so one asset may be shown at different sizes or with different Footer/Description text in different durable contexts. Checkpoints retain their existing immutable asset safety store, while Current, Cuts, Review snapshots, backups, and package verification all treat image references as durability-critical. Checkpoint and History comparison render image occurrences as real figures and compare asset identity, size, Footer, and Description; History serves old image bytes from the verified immutable revision rather than depending on Current assets.

## Manuscript toolbar surface (8.19.3)

The Manuscript toolbar reserves top-level space for persistent manuscript tools and side panels. Find and replace is a View-menu command (with Cmd/Ctrl+F unchanged), while the single project bookmark is surfaced as a quiet viewport indicator only when one exists. Bookmark breadcrumbs in the left Manuscript navigator are navigation affordances; removal is explicit from the viewport bookmark context menu or the existing selection/structural actions. The writer-facing Markup surface is named **Markers** while existing internal IDs and persistence fields remain unchanged. Toolbar order places Reviews after Checkpoints and before Cuts. Reviews is a first-class Manuscript working layer for processing existing Review rounds; Image is a first-class contextual manuscript inspector.

## Browser automation readiness (8.18.27)

`boot()` completion is an explicit testability boundary. `window.__breadmakerBootPromise` resolves only after workspace/UI restoration and final route activation; successful completion sets `data-breadmaker-boot-ready=true` on the root element. This metadata is inert in normal use and prevents browser automation from confusing early rendered content with a fully initialized application.

## 8.18.25 — Browser flake containment without hiding failures

Current-browser verification remains one pytest item per fresh process. Initial browser failures receive one end-of-stage fresh-process retry; the verifier mutates only the logical final status while retaining initial failure evidence in `verification-report.txt`. Unit/Core/API remain non-retrying gates and Durable E2E retains its own sequential recovery semantics. Recovered retries are successful releases with explicit retry accounting, not silent passes.

## 8.18.24 — Unit contracts mirror production semantics

The first-class Unit gate is authoritative only when its expectations are derived from established production contracts. Unit tests must not invent stricter or different semantics than the integration/API layers they supplement. Browser tests should likewise own one behavior boundary: focused interaction contracts may enter the required workspace deterministically when top-level navigation is already protected elsewhere.

## 8.18.23 — Layered verification: Unit → Core/API → Current Browser → Durable E2E

Release verification now has four explicit behavioral layers. Unit tests are the first executable verification gate and cover pure/deterministic contracts without FastAPI `TestClient`, project-database lifecycle, or Chromium. Core/API retains integration and HTTP/persistence contracts. **Both browser layers are end-to-end browser tests; their names describe execution lifecycle, not test scope.** Current Browser protects today's UI behavior with one fresh server/Chromium process per item, parallel isolation, and one fresh-process retry. Durable E2E protects long-lived historical workflows inside the shared sequential `tests/test_e2e.py` server/Chromium lifecycle. The verifier reports item accounting for every layer independently so a green aggregate cannot hide which layer actually ran.

Unit tests have a 3-second per-item setup/call/teardown budget; Core/API remains 10 seconds and browser/E2E remains 15 seconds. `tests/conftest.py` lazily imports the application only when an API fixture requests it, preventing unit collection/execution from paying full workspace startup cost.

## 8.18.22 — Tiered per-item test budgets

Release verification now applies a 10-second core/API budget and a 15-second browser/E2E budget across setup + call + teardown. Browser tests remain isolated where required and must still prefer observable completion signals over fixed sleeps; the larger browser budget is headroom for real browser/server lifecycle variance, not permission to accumulate timing sleeps.

## 8.18.21 — Test responsibility boundaries

Durable historical workflows should not duplicate browser-actionability assertions already covered by focused current-browser contracts. Once a durable workflow has proved the application captured the intended selection state, downstream structural actions may be dispatched through their real DOM handlers so floating-toolbar positioning heuristics do not obscure the domain contract under test.

## 8.18.20 — Semantic reliability contracts

Release verification distinguishes product behavior from test-fixture semantics. Remote-history repair tests must republish the exact previously published revision whose remote payload was lost; publishing a newer revision is a fresh sync. Manuscript selection tests may construct an exact DOM Range for deterministic automation, but must cross Breadmaker's real pointer-release selection boundary before invoking UI actions. Map direct-manipulation tests target semantic Line positions through the current layout plan rather than inferring logical positions from rendered SVG route segmentation.

## 8.18.16 — Browser testability boundary

Browser automation has a stable, product-neutral locator layer. `data-testid` identifies transient or structurally mutable interaction surfaces while product IDs/data attributes retain their runtime meaning. Tests synchronize on the state they claim to protect—application state for mode/view transitions, API state for persistence, and explicit visibility for transient UI—rather than treating elapsed time as proof of completion.

Historical durable workflows should be one behavioral contract per pytest item where practical. Stress/property gates may increase shard count while preserving their exact aggregate workload so setup + call + teardown retain deterministic headroom inside the unchanged 10-second item budget.

## 8.18.15 — Durable interaction fidelity

Durable browser workflows must enter the same interaction lifecycle as current production UI. Programmatic manuscript selections emit `selectionchange` before using the selection toolbar; integrated Maps workflows isolate direct-manipulation assertions from geometry they already transformed earlier in the same test; rendered typography assertions may reflect intentional CSS text transforms. The verifier architecture and product behavior remain unchanged.

## 8.18.14 — Durable historical-contract policy

Durable browser workflows must assert the current product contract and synchronize on the product state they claim to protect. Programmatic manuscript selections explicitly position the selection toolbar, asynchronous pointer persistence waits on persisted API state, and historical menu/verification assertions follow current UI ownership.

## 8.18.13 — Durable E2E editor-lease lifecycle

Durable browser tests keep production single-editor lease semantics enabled. The harness uses one synthetic client identity for both fixture-side project activation and browser-side API traffic, and cleanup follows stop-heartbeat → drain → release-lease → close-page ordering. This preserves real lease protection while preventing harness-only cross-item races.

## 8.18.12 — Durable runtime sharing without cross-test project state

The historical durable browser suite shares uvicorn and Chromium startup cost, not mutable product data. An autouse fixture activates a new Breadmaker project for every pytest item so contracts are deterministic regardless of source order while persistence and multi-step state remain fully real inside each item. Static browser-shell preparation is cached, but each item still opens an isolated page.

The hard per-item execution ceiling remains 10 seconds. Any durable call-phase timeout is considered a browser-process containment boundary after its failure record is persisted: the worker terminates and the verifier resumes only unrecorded items in a clean process. This prevents one timed-out synchronous Playwright call from manufacturing a tail of unrelated timeouts while retaining complete item accounting.

## 8.18.10 — Durable item isolation inside the shared E2E lifecycle

The durable suite still owns one sequential server/Chromium lifecycle, but large end-to-end workflows must fit the same 10-second item budget as every other pytest item. Coverage is split at stable workspace boundaries instead of increasing that budget. The emergency watchdog unwind window is cleanup-only and intentionally exceeds Playwright's 5-second locator ceiling; immediately before any last-resort process exit, the watchdog writes the exact timeout phase/reason to the verifier JSONL stream.

## 8.18.9 — Durable interaction and failure-accounting boundary

Canvas viewport persistence is origin-bound: a delayed save may only write UI state for the Canvas that queued it. Canvas switching cancels any pending delayed save, persists the outgoing Canvas synchronously, then restores the incoming Canvas. This prevents cross-Canvas tool-mode/zoom/viewport contamination without removing local viewport memory.

Experimental Metro verification must satisfy the same two gates as production: the project feature flag and the explicit renderer opt-in. Durable tests protect current interaction outcomes rather than retired affordances (for example, Connector endpoint retargeting and direct Card drawing).

The ten-second item budget remains unchanged. If an item has already timed out, teardown first receives a bounded cleanup grace and a Python-level interrupt so pytest can emit a teardown result and continue. A second bounded grace retains `os._exit(124)` as the last-resort containment boundary for teardown code that cannot be interrupted. Incremental JSONL remains authoritative for partial-run accounting. Durable expected-item counts are derived from every pytest test function in the module, not from a naming subset, and the final console digest surfaces expected-vs-recorded mismatches explicitly.

## 8.18.8 — Verification continuation boundary

A pytest item still fails at the hard ten-second execution deadline. After the failure is raised, teardown receives a small bounded cleanup window so Chromium/TestClient resources can unwind and a sequential durable module can proceed to later items. This cleanup window is not additional test execution time: a timed-out item remains failed. The outer verifier process watchdog remains the terminal containment boundary for genuinely wedged cleanup.

Historical durable E2E contracts must follow current user-facing interaction primitives when an older control has been intentionally retired. They continue to protect the same behavioral outcome rather than fossilizing obsolete selectors.

## 8.18.7 — Verification result durability and UI interaction contracts

The release verifier records each pytest setup/call/teardown report incrementally to a temporary JSONL stream as well as JUnit. Aggregate browser modules therefore have durable child-level accounting even if a later test, fixture teardown, or process timeout prevents JUnit finalization. Each planned job has an expected item count; a mismatch is itself a release-gate failure.

The hard per-item contract measures only real setup/call/teardown execution: 10-second core/API and 15-second browser/E2E budgets. Pytest traceback/report construction is outside the alarm window, preventing a slow failure formatter from replacing the original product assertion with a secondary timeout. Review UI behavioral contracts now explicitly include the Details pencil and current Questionnaire/feedback/lifecycle interaction model rather than fossilized controls removed during 8.17–8.18.

## 8.18.6 — Verification fidelity boundary

Ordinary core/API contracts run as parallel pytest **module jobs** to amortize Python/FastAPI startup cost, while stress-oriented reliability and scale contracts remain exact-item isolated. The hard pytest-item watchdog remains authoritative: 10 seconds for core/API items and 15 seconds for browser/E2E items. Each verification process writes JUnit results that the orchestrator parses into per-test pass/fail/skip counts and child failure records, so an aggregate sequential workflow can no longer collapse dozens of failures into an opaque progress string. Failure phase markers distinguish setup, test body, and teardown failures without restoring noisy full console tracebacks.

## 8.18.5 — Verification execution model

The release gate distinguishes **isolatable current contracts** from **stateful durable workflows**. Core/API items and current browser-control contracts remain independently isolated. `tests/test_e2e.py` is a deliberate sequential workflow module with module-scoped uvicorn/Chromium fixtures; splitting its functions into unrelated subprocesses changes its test preconditions and creates false failures, so it runs as one bounded pytest process. The per-item 10-second watchdog still applies inside that process, while the verifier adds a larger outer process watchdog for poisoned fixtures or Chromium teardown.

Console reporting is two-tiered: the default path prints a compact grouped failure digest after all stages and preserves full traces in `verification-report.txt`; `--verbose-failures` opts back into complete console traceback output. This keeps the actionable summary at the bottom of the terminal even when many tests fail.

## 8.18.4 — Verification architecture

Release verification is an explicit multi-layer regression gate rather than a sequential list of historical suites. Source/locale/browser preflight runs first; core/API modules then execute in isolated parallel pytest processes; current browser contracts and durable end-to-end workflows execute as one browser test per process with limited parallelism. The process boundary is the containment mechanism for workspace singletons, uvicorn servers, Playwright drivers, and hard watchdog termination. The verifier discovers current 7.x/8.x tests automatically and self-checks that its manifest has not omitted newly added current contracts. A current UI control-audit layer validates visible behavior rather than function names, specifically protecting against controls that render correctly but lose their event wiring.

## 8.18.3 — Review scope presentation

Review scope remains stored by stable IDs. The overview derives a compact positional presentation from current manuscript order; this is UI-only and does not change scope identity. The complete human-readable scope remains available as hover text.

## 8.18.2 — Collection rail quick-create affordance

Canvas, Maps, and Reviews share one end-of-list quick-create primitive. It is intentionally a secondary creation path only; collection `…` menus remain authoritative for import/populate/other collection actions. No storage or API changes.

## 8.18.1 — Toolbar empty-state consistency

Workspace toolbar titles distinguish collection labels from renameable objects consistently: collection placeholders are muted and never expose rename affordances. No storage or schema change.

## 8.18.0 — Review round lifecycle

Review rounds are immutable-baseline containers with reversible availability state:

- `open`: the single live in-app round; participant writes target this frozen snapshot.
- `paused`: not writable by live reviewers; its baseline may be refreshed when the existing safety gates allow it.
- `closed`: historical and not live-writable, but fully navigable, reopenable, and valid for late Bread Review imports.

Starting a new round closes any Open/Paused prior baselines and creates new checkpoint(s). Reopening an older Closed/Paused round reuses its exact snapshot/checkpoint set and pauses any currently Open round; it never creates replacement history. Portable Bread Review import is keyed by `review_id + snapshot_id + participant_id`, not by current round state, so late returned files merge into the correct historical round.

## 8.17.9 — Portable Bread Review return path

A dropped `.breadreview` remains a temporary portable session until the user chooses what to do with it. If its `project_id` matches the active project, Breadmaker offers both paths:

- **Open as Reviewer** keeps the existing temporary portable-review workflow for testing and editing.
- **Import Returned Feedback** merges the package through the same `_merge_breadreview_manifest` validation/idempotency path used by file import, then closes the temporary package.

This deliberately avoids auto-importing same-project packages: local testing must remain possible. Temporary Reviewer Mode always writes/downloads the current Bread Review package on Finish or Exit, even when no new dirty flag was recorded during that session.

---

# Retained storage/recovery architecture contracts

The following 7.x contracts remain authoritative. They are retained here because later feature work must not silently weaken the durability model.

## 7.4.1 recovery/portability boundary

Recovery state, portable project packages, local revision history, and remote backup providers are distinct responsibilities. Provider implementations may publish and retrieve immutable backup generations, but they do not own canonical project state.

## 7.7.0 Canonical generation authority

Canonical writes advance one project generation transactionally. **Canonical generation never decreases.** Derived history, backup metadata, UI state, and verification bookkeeping do not become competing generation authorities.

## 7.7.1 Frozen canonical snapshot boundary

Backup and history publication operate on a frozen canonical snapshot. **Package construction never reads live Current** after the freeze boundary; every artifact in one publication is derived from the same captured generation/fingerprint.

## 7.7.2 Publication state machine

The publication progression remains conceptually `SNAPSHOTTED → PACKAGED → LOCAL_VALIDATED → TRANSFERRED → DESTINATION_VERIFIED → PUBLISHED`. Only **PUBLISHED** is an authoritative recovery point. A destination can reach **DESTINATION_VERIFIED** without yet becoming the published generation, and late completion of an older generation may not regress the published pointer.

## 7.7.3 Immutable revision identity

**Local Revision Store v1** is the retained local history contract for this boundary. Published revision generations are immutable identities. Retry, duplicate upload, delayed completion, and timeout-after-success paths must converge on the same generation rather than manufacturing a second logical revision. The revision transport/store boundary has **no deletion or GC capability**; conservative cleanup is owned separately by History GC policy.

## 7.7.4 restore concurrency

History Restore applies through a compare-and-set boundary: restore is rejected if the canonical generation changed after the restore source was chosen. A successful restore creates a later canonical generation rather than mutating history in place.

## Remote Revision History

Remote Revision History is derived from immutable published revision manifests. The remote `manifest`/listing is validated rather than trusted as canonical project state, and `LAST`/latest-style pointers are rebuildable convenience metadata rather than authority.

## Conservative History GC

**Conservative History GC** deletes only revision material proven unreachable under the current retention/publication model. Ambiguous ownership or incomplete verification fails closed and retains data.

## 7.7.8 Soak, Disaster Recovery & Release Hardening

Cross-layer soak verification is bounded and **Deterministic** where reproducibility matters. The hard 10-second core/API per-test rule remains in force; browser/E2E contracts use 15 seconds. This hardening work **does not change Revision Store format** merely to make tests or release packaging easier.

## 7.7.9 Final 7.7 Integration / Format Freeze

The 7.7 integration boundary froze the then-current Revision Store and publication formats so later releases could evolve implementation without accidental format drift.

## 7.8.6 Final current-format storage audit

Breadmaker's current-only pre-alpha policy supersedes the **4.12 preservation architecture (historical; removed in 7.8.1)**. Unsupported old schemas are rejected rather than migrated, while current-format storage, recovery, erasure boundaries, revision history, and backup publication remain internally consistent.


### Hosted container image baseline

The hosted image pins the explicit `python:3.14.7-slim-trixie` base tag. Patch/base-image changes are release changes; do not use a floating `python:3.14-slim` tag. The image build also fails if ImageMagick, libheif, or pillow-heif becomes available in the hosted container.


### Archive image ingress

The hosted image ceiling applies before image bytes are materialized from Cuts, portable BreadReview, and native project-package imports. Hosted project packages are rejected before activation if any `assets/` member exceeds the configured image ceiling or is not a structurally valid PNG/JPEG/WebP. Portable BreadReview decoding receives the same per-asset ceiling. These archive paths do not create a decoder exception.

</details>
