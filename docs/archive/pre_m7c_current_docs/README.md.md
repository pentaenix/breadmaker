## 10.3.59 — Milestone 7A Identity Platform runtime checkpoint

M7A is a deliberately reversible checkpoint on top of the M5 cloud-native runtime and M6 GO decision. Hosted mode can now select Google Identity Platform behind `BREADMAKER_IDENTITY_PLATFORM_ENABLED`, exchange a fresh provider ID token for a server-owned session cookie, enforce recent authentication and private-alpha admission, verify subsequent cookies with revocation checks, and derive Breadmaker authorization only from the mapped internal `usr_…` identity. Provider tokens, passwords, TOTP secrets, and invitation plaintext are not Breadmaker persistence. Auth0 remains available as rollback until M7C; M7B owns provider UI/MFA/linking/security-route parity.

This release also carries the final local privacy corrective from the 10.3.57 gate: Delete All detaches process-global storage from the old project before erasure and removes any stale project directory recreated before privacy barriers reopen. Local Schema 42, the verifier/watchdogs, and normal accountless local startup remain unchanged.

## 10.3.59 — Serverless hosted rewrite Milestone 5B: behavioral parity gate

Milestone 5B closes the active Milestone-5 application-integration scope on top of the 10.3.53 M5A cloud-native composition. Hosted canonical state remains Firestore/GCS-authoritative (or deterministic memory fakes in `hosted_dev`), while local Breadmaker remains on the user-verified 10.3.52 Schema-42 SQLite/filesystem runtime and verifier harness.

This round adds behavioral conformance rather than more architecture: Firestore-only annotation batches publish atomically with one project-generation advance; immutable checkpoint bytes remain immutable while deleting a checkpoint removes only the authoritative Firestore pointer and leaves the object as a retention/GC candidate; per-member bookmark/UI state is membership-authorized; chapter block replacement removes omitted hosted blocks; Canvas/Map Populate uses the real source-controlled story-template catalog and project chapter/scene data; and hosted Map auto-arrange now updates canonical map-item layout instead of returning a fake no-op success. Additive credential-free M5 conformance tests cover project lifecycle/membership/member state, manuscript hierarchy, annotations/comments, reviews, canvases/maps/tasks, immutable checkpoint semantics, M5A package/import/export and unauthorized-access contracts.

Backups/account deletion/history restore remain explicit Milestone-9 capability boundaries, and horizontal/process-loss hardening remains Milestone 8. No live GCP resource is created here. The user, not the model/agent, runs the full Editor + Cloud verifier to close Milestone 5.

## 10.3.53 — Serverless hosted rewrite Milestone 5A: cloud-native runtime composition

Milestone 5A is the first integration checkpoint: it wires the verified Firestore and Cloud Storage adapters into a genuinely cloud-native hosted product composition. `hosted_dev` uses deterministic in-memory Firestore/GCS fakes; real `hosted` mode selects the Google drivers. Hosted startup no longer instantiates the local Workspace/SQLite project stack, local recovery/history workers, project directories, or native persistence bridges. Local Breadmaker remains on the all-green Schema-42 SQLite/filesystem composition.

Canonical hosted child mutations advance project generation in the same Firestore transaction as the entity mutation. Hybrid prose/snapshot payloads publish immutable verified object bytes before the authoritative Firestore pointer/CAS. Hosted `.breadmaker` export excludes membership/account authority; import always allocates a fresh project ID. Existing package-v2 SQLite is supported only as a transient in-memory decoder during import and is never retained as hosted persistence. Milestones 3 and 4 are now treated as frozen adapter layers.

The 10.3.52 verifier/harness is a protected baseline for this migration: verifier orchestration, watchdogs, local runtime, local requirements, and the frozen Firestore/GCS adapter implementations remain unchanged. Milestone-5 coverage is additive and credential-free. No live GCP resource is created by this release. The hosted HTTP boundary now statically covers all 287 routes in the current API manifest, but 10.3.53 is deliberately an M5A checkpoint rather than the final Milestone-5 gate; behavioral parity/conformance continues in the next submilestone.

## 10.3.52 — Serverless hosted rewrite Milestone 4: Cloud Storage object adapter

Corrective note: 10.3.52 keeps the verified Milestone-4 storage implementation unchanged. The 10.3.50 user run confirmed the corrected PNG fixture and database-health path, leaving six unrelated hard 10-second Core/API timeouts plus one Durable-E2E Find timing/state race. This release moves exactly those six repeat timeout modules into the existing one-worker no-contention lane without raising the 10-second watchdog, and makes Find report a pending `… / …` / `aria-busy=true` state while its debounced save/search request is in flight instead of falsely showing `0 / 0`.

Milestone 4 implements hosted immutable content/revision/asset storage behind the frozen serverless persistence contracts. The new `src/hosted/storage` package provides canonical server-generated GCS keys, explicit generation preconditions, bounded streaming, exact-generation SHA-256 verification, pointer-last Firestore publication, deterministic orphan discovery/cleanup contracts, and a cloud-backed implementation of the logical `AssetStore` port. Credential-free release coverage uses `MemoryObjectStore`; the real Google Cloud Storage SDK stays isolated in `src/hosted/storage/google_driver.py` and `requirements-hosted.txt`. The current hosted product routes/runtime are intentionally not switched to these adapters until Milestone 5. Local `./run`, Schema 42, SQLite/filesystem persistence, `LocalAssetStore`, and local requirements remain unchanged. The user, not the model/agent, runs the release verifier.

## 10.3.47 — Serverless hosted rewrite Milestone 3: Firestore structured-state adapter

Milestone 3 implements the server-only Firestore structured-state boundary frozen by the Milestone-2 data model. Hosted users, provider bindings, projects, memberships, and structured project entities now have transaction/CAS/idempotency repositories plus a deterministic in-memory Firestore-semantic driver for credential-free release tests. The real Google SDK is isolated behind `src/hosted/firestore/google_driver.py` and appears only in `requirements-hosted.txt`; local `./run`, Schema 42, SQLite/filesystem persistence, and local requirements remain unchanged. Hosted application integration is intentionally deferred to Milestone 5.

## 10.3.43 — Serverless hosted rewrite Milestone 1: persistence seams

Milestone 1 begins the undeployed serverless hosted rewrite while keeping local Breadmaker on Schema 42, SQLite/filesystem persistence, accountless startup, and zero cloud dependencies. Environment-neutral runtime ports now own project catalog/membership, logical asset bytes, project usage/quota projection, and avatar reads. Native filesystem/history/backup operations are isolated behind explicit `Native*` bridges and a machine-readable dependency inventory that may only shrink in later milestones. No Firestore, Cloud Storage, Firebase, or Identity Platform runtime dependency is added in this milestone. The user, not the model/agent, runs the release verifier.

## 10.3.41 — Checkpoint 12: staging activation

Corrective note: 10.3.41 fixes the local macOS launcher under the system Bash 3.2 shipped by Apple. The launcher no longer expands a possibly-empty Bash array under `set -u`; Uvicorn arguments are assembled with positional parameters instead. Checkpoint 12 hosted/runtime behavior and Schema 42 are otherwise unchanged.

Checkpoint 12 activates the already-hardened Auth0 path for a real **staging-only** hosted runtime. `BREADMAKER_RUNTIME=hosted` requires `BREADMAKER_ENV=staging`, Auth0 authentication, HTTPS public origin, Secure cookies, Host pinning, and exactly one worker; production remains explicitly disabled. Hosted secrets are now resolved through a strict `secretmanager:` boundary using the staging workload's Google Application Default Credentials. Normal `./run` remains local, accountless, and requires no secret or external account.

The hosted image is separate from local dependencies (`requirements-hosted.txt`), so the downloadable editor does not acquire a Google Cloud dependency merely because staging exists. Load-balancer health uses a minimal host-independent `/healthz` response while application traffic retains exact public-Host enforcement. Checkpoint 12 also fixes a latent real-provider bug by importing `new_login_browser_binding` where the live login routes use it. Schema 42 is unchanged.

## 10.3.37 — Checkpoint 11 pre-deployment security gate
Verification-only corrective: 10.3.37 keeps the prior runtime/security behavior and narrows the toolbar race contract to reject executable `requestAnimationFrame(` calls rather than matching prose comments.

Checkpoint 11 removes the temporary auth UI preview completely and freezes the real Auth0 path for adversarial testing. Schema 42 remains current-only; direct project routes authorize membership, redirect handling rejects browser-normalization tricks, account linking compensates provider-side partial commits, and TOTP replacement prepares enrollment before deleting the working factor. The final consultant pass also bounds locale restart state, retires superseded OIDC/browser-binding transactions, rate-limits locale restarts, rejects ambiguous Host fields, pins Auth0 discovery endpoints to the issuer origin, and prevents wrong-session callbacks from consuming link/step-up state. Local Breadmaker remains accountless.
Authenticated link/step-up transaction stores also enforce per-user transaction ceilings, and failed wrapper binds retire their underlying OIDC state instead of consuming shared capacity until TTL expiry.
The final corrective pass also positions the floating manuscript selection toolbar before it becomes interactive, removing the delayed-bookmark-tooltip hover race without shortening the 300 ms UX delay.


## 10.3.27 — Checkpoint 10.6 corrective verification


## 10.3.26 — Checkpoint 10.6: stable project identity and hosted project URLs

Schema 42 intentionally breaks every earlier pre-alpha project. Each current project now stores one immutable 32-hex `project_uid` inside SQLite; it must exactly match the portable manifest `project_id` and workspace folder/registry id. There is no migration or compatibility path. Hosted project URLs use `/p/:projectId` (plus `/canvas`, `/maps`, `/tasks`, `/reviews` suffixes), direct links resume the requested authorized project, and the hosted root resumes the account's last-opened project. An account with zero projects receives an in-memory blank manuscript; the first meaningful write creates the project, assigns its ID, persists the draft, and replaces the URL. The existing All Projects modal remains the only project browser. Local Breadmaker keeps its open-and-write behavior and does not gain authentication.

## 10.3.25 — Identity Checkpoint 10.5: authentication and security UI

Hosted Auth0 mode now has its product UI: the paired Cloud desired state styles New Universal Login to the approved Breadmaker mockup (Editor-native Light page/surface hierarchy (#F3F3F1 / #FFFFFF / #F8F8F7), #202020 primary action/text, and #DEDEDB/#C9C9C5 separators) while Auth0 continues to own the email/password, social, TOTP, and recovery prompts. The login prompt is intentionally **Identifier + Password** so email and password stay together. Public login/signup responses remain generic enough to avoid confirming whether an email exists, and the paired Auth0 attack policy uses a humane 15 failed attempts per identifier+originating IP rather than a three-to-five-attempt or account-wide lockout.

The mockup’s top-right **Sign up** is also preserved safely: `/auth/signup` starts the same Auth0 Authorization Code flow with `screen_hint=signup`; the Auth0 page-template link is supplied from trusted application metadata rather than request/query input or widget DOM rewriting.

Inside the authenticated app, the profile menu now opens an Account & Security modal for linked sign-in methods, optional social TOTP, database password handoff, owned-session revocation, and sign-out-everywhere. The UI only initiates the Checkpoint 9/10 server flows: it never receives passwords, TOTP seeds/codes, provider tokens, Management API credentials, or server-only step-up grants. Local Breadmaker remains accountless and renders none of this surface. **Project IDs and `/p/:id` routing remain Checkpoint 10.6; the existing All Projects modal remains sufficient for project switching in 10.5.**

## 10.3.24 — Identity Checkpoint 10: recovery, step-up, and security operations

Checkpoint 10 freezes the **server-side security mechanics only**; product UI is deliberately deferred to Checkpoint 10.5. `src/auth/security.py` adds fresh Auth0 reauthentication transactions and five-minute server-only grants bound to the exact Breadmaker user, session, operation, and target. Sensitive identity/MFA/password/session mutations cannot be authorized by browser assertions or by merely having a recently issued cookie.

The hosted-development Auth0 composition can now inspect linked identities and owned Breadmaker sessions; unlink a non-primary/non-last identity with rollback-safe local/provider ordering; hand authenticated database-password changes to a five-minute Auth0 password-change ticket; enable/replace/disable optional social TOTP while keeping TOTP seeds/codes and recovery codes inside Auth0; revoke an owned Breadmaker session; and sign out everywhere by revoking Auth0 sessions before Breadmaker sessions. Database/password identities may never disable mandatory TOTP. Social TOTP enrollment uses a short-lived HMAC-signed authorization intent verified by the Auth0 Post-Login Action. **No Security UI is included in this release; Checkpoint 10.5 will present these already-frozen operations.** Normal `BREADMAKER_RUNTIME=local` remains accountless, and staging/production hosted login remains disabled.

## 10.3.23 — Identity Checkpoint 9: explicit dual-proof account linking

Checkpoint 9 adds the server-side account-linking boundary without adding product UI. Linking begins only from a recently authenticated Breadmaker account, binds the transaction to that exact session, and forces a separate fresh Auth0 OIDC authentication (`prompt=login`, `max_age=0`) for the identity being attached. Primary and secondary Auth0 identifiers are derived only from trusted server state; email is never searched, compared, or accepted as linking authority.

Provider-side mutation is owned by a dedicated server-only Auth0 Management API operator restricted to `update:users`. Its credentials are separate from the normal hosted-web Auth0 client and deployment credentials and remain outside OpenTofu/browser state. If the proven secondary identity already belongs to a different Breadmaker `usr_` account, the operation fails before Auth0 is changed: Checkpoint 9 never silently merges profiles/projects. Successful links rotate the current Breadmaker session. **No linking UI is included; that is deliberately deferred to Checkpoint 10.5 after the Checkpoint 10 recovery/step-up security surface exists.** Staging/production hosted login remains disabled.

## 10.3.22 — Identity Checkpoints 7+8: database/TOTP + real localhost Auth0 sandbox

Checkpoint 7 and Checkpoint 8 are intentionally shipped together so TOTP has one owner and one policy. Auth0 Universal Login owns database passwords, password hashes, TOTP secrets, OTP challenges, enrollment, and recovery codes; Breadmaker never receives or persists those materials. `src/auth/database.py` maps only verified Auth0 `auth0|…` subjects with verified email into the existing `(issuer, subject)` account registry. Google/Apple remain issuer+subject identities and never auto-link by email.

The paired Auth0 Post-Login policy makes TOTP mandatory for database/password identities: users without OTP enroll it and users with OTP are challenged. Google/Apple remain single-factor by default; once an OTP factor exists in Auth0, subsequent social logins challenge it. Social enrollment management belongs to the later Security UI checkpoint; Checkpoint 8 freezes the sticky enforcement rule without duplicating MFA state in Breadmaker.

`./run-hosted-auth-dev` is the only local launcher for real Auth0. It composes `BREADMAKER_RUNTIME=hosted_dev`, `BREADMAKER_AUTH_MODE=auth0`, development-only loopback callbacks, and a separate `.dev/hosted-auth-data` home. Normal `./run` remains accountless and still uses null account/session services. **Hosted login remains disabled for staging/production**; this release exposes only the explicit localhost development sandbox.

## 10.3.21 — Identity Checkpoint 6: Apple mapping + markup-hover reliability

Checkpoint 6 adds `src/auth/apple.py`, a dormant Apple identity mapper behind the existing Auth0 + generic OIDC boundary. Breadmaker accepts Apple only after the Auth0 ID token has already been verified; the mapper requires Auth0's `apple|…` subject form and keeps the full verified `(Auth0 issuer, Auth0 subject)` pair as the external identity key. Hide My Email is a first-class privacy choice: both legacy `@privaterelay.appleid.com` and 2026 `@private.icloud.com` Sign in with Apple relay addresses are accepted as profile metadata and never become identity/linking keys. First-authorization profile data is preserved when later Apple logins omit it.

**Hosted login remains disabled.** There are still no `/auth/login` or `/auth/callback` routes and no login UI. Apple signing keys/provider tokens remain outside Breadmaker runtime. Email/password, TOTP, account linking, recovery UI, and live Auth0 interaction remain later checkpoints. Local Breadmaker remains authentication-free.

This release also removes the remaining scheduler-sensitive 340 ms assumption from the historical Manuscript markup-margin hover E2E. The product keeps its 300 ms hover delay; the durable test now waits up to 1500 ms for observable visibility, matching the marker/checkpoint reliability pattern.

## 10.3.20 — Identity Checkpoint 5: Google mapping

Checkpoint 5 adds `src/auth/google.py`, a dormant Google identity mapper behind the existing Auth0 + generic OIDC boundary. Breadmaker accepts Google only after the Auth0 ID token has already been verified; the mapper requires Auth0's `google-oauth2|…` subject form and keeps the full verified `(Auth0 issuer, Auth0 subject)` pair as the external identity key. Email and display name remain mutable profile metadata and matching email never merges accounts. Google Drive authorization, additional Google API scopes, and provider token persistence remain separate from hosted login.

**Hosted login remains disabled.** There are still no `/auth/login` or `/auth/callback` routes and no login UI. Apple, email/password, TOTP, account linking, recovery UI, and live Auth0 interaction remain later checkpoints. Local Breadmaker remains authentication-free.

## 10.3.19 — Identity Checkpoint 4: Auth0 boundary

Checkpoint 4 adds a dormant, server-side Auth0 application boundary on top of the generic OIDC engine. `src/auth/auth0.py` freezes development/staging/production separation, exact `/auth/callback` routing, regular-web/Authorization-Code assumptions, the minimal `openid profile email` scope set, Universal Login expectations, and runtime client-secret injection by symbolic secret reference. **Hosted login remains disabled**: there are still no `/auth/login` or `/auth/callback` routes, provider connections, password/TOTP flows, or login UI. Local Breadmaker remains authentication-free.

## 10.3.18 — marker hover reliability corrective

10.3.18 fixes the final durable-E2E timing race in marker-description hover without changing the 300 ms product delay. Marker hover liveness is owned by mouse enter/leave events rather than a delayed CSS `:hover` query, and the durable browser contract waits for observable visible/hidden state instead of assuming a 40 ms scheduler margin. Security hardening, local-vs-hosted image boundaries, and Identity Checkpoint 3 remain unchanged.

## 10.3.17 — corrective security-contract synchronization

10.3.17 keeps the 10.3.15/10.3.16 dependency and hostile-image hardening intact while reconciling five historical contracts exposed by the full suite. Route verification now follows FastAPI's supported effective OpenAPI surface; the old cryptography test protects the fixed security floor instead of requiring a vulnerable exact version; identity invariants remain documented; and local-vs-hosted image picker behavior is separated correctly. Hosted login remains disabled and Identity stays at Checkpoint 3.

## 10.3.16 — verification interpreter hardening

10.3.16 keeps the 10.3.15 security and image boundary unchanged while making release verification deterministic across machines with multiple Python installations. The verifier selects Python 3.10+, propagates that exact interpreter through bundle/Editor/Cloud gates, and automatically discards stale `.venv` environments created with an older Python. Production dependency pins are unchanged.

## 10.3.15 — hostile-image boundary hardening

Hosted project-image ingestion now accepts only structurally validated PNG, JPEG, and WebP; HEIF/HEIC/AVIF and broad decoder stacks are outside the hosted trust surface. Hosted uploads default to 12 MiB with a 25 MiB hard configuration ceiling, 8192 px per axis, and 20 megapixels. Validation reads container structure and geometry without decoding pixels. Hosted project-image decoding is disabled in the main process; PDF/DOCX exports use image placeholders until a separate disposable sandbox worker exists. Pillow is explicitly pinned to 12.3.0 and the hosted container build rejects ImageMagick, libheif, and pillow-heif. Identity remains at Checkpoint 3 and hosted login remains disabled.

## 10.3.14 — dependency and request-boundary security hardening

10.3.14 closes the known dependency vulnerabilities identified after Identity Checkpoint 3. The hosted framework/parser/crypto/PDF/JOSE stack is pinned to patched current releases; security-sensitive routing now uses the canonical ASGI scope path rather than a Host-reconstructed URL; release verification requires exact dependency versions and performs a fail-closed `pip-audit` before pytest. Runtime source contracts continue to forbid direct eval/exec/pickle/shell execution primitives, and the existing streamed hosted body ceiling remains mandatory. No new login/provider capability is enabled; Identity Checkpoint 3 remains the active authentication milestone.

## 10.3.13 — Identity Checkpoint 3: generic OIDC engine

Breadmaker now contains a provider-neutral, server-side OIDC/BFF protocol engine for hosted mode: Authorization Code + PKCE S256, single-use state, nonce validation, discovery, server-side token exchange, JWKS/RS256 verification, issuer/audience/azp/time validation, and signing-key refresh. **Hosted login remains disabled**: there is no Auth0 adapter or login/callback route yet, and local Breadmaker remains authentication-free. JOSE/JWT signature and OIDC claim validation are delegated to pinned Authlib 1.8.0 / JOSERFC rather than hand-rolled cryptography. The engine is exercised only through a deterministic fake OIDC issuer/JWKS harness until the next provider checkpoint.

## 10.3.12 — hosted internal identity and session foundation

10.3.12 implements Checkpoint 2 of the managed-service identity plan without enabling hosted login. The hosted session runtime now has a separate external-identity registry keyed only by `(issuer, subject)`, while Breadmaker keeps its stable internal `usr_…` user id as the product identity. Email is metadata and is never searched to resolve or merge an account. Opaque sessions now support explicit login replacement/rotation, per-user revocation, non-secret session summaries, and trusted `authenticated_at` timestamps for later step-up checks.

**Local Breadmaker remains authentication-free.** The local runtime still uses `NullSessionService` plus `NullAccountIdentityService`; it does not create or read the hosted account registry and requires no Breadmaker account, subscription, or network identity. Breadmaker Cloud Backup remains a separate future paid capability and does not convert local editing into hosted authentication. Auth0/OIDC, Google, Apple, passwords, TOTP, account linking, and hosted-login UI are still absent.

## 10.3.11 — checkpoint hover reliability

10.3.11 is a narrowly scoped editor reliability patch after the final refactor verification. Checkpoint description hover cards still use the established 300 ms quiet-hover delay, but the delayed callback now trusts the row's enter/leave ownership token instead of re-querying the browser `:hover` pseudo-state. The durable browser regression also waits for the observable tooltip state with a bounded timeout rather than assuming a 300 ms timer must execute inside a 40 ms scheduling margin. The 10.3.10 CSS documentation-contract correction remains intact; no backend, schema, persistence, CSS ownership, or API behavior changes are introduced.

## 10.3.10 — documentation contract correction

10.3.10 corrects the wording of the frozen CSS cleanup policy after the fully green browser/E2E run exposed one historical documentation assertion. The policy remains unchanged: **do not infer dead selectors from source absence**. There are no runtime, ownership, CSS, backend, schema, storage, or API behavior changes from 10.3.9.

## 10.3.9 — final frontend ownership cleanup and architecture freeze

10.3.9 closes the frontend refactor by correcting the last cross-feature ownership leaks without changing product behavior. Application-wide Undo/Redo and structural Undo coordination now live in `static/js/core/history.js`; `features/manuscript/history.js` retains only Manuscript edit-snapshot capture/restoration. `currentView()` moves to routing, Manuscript UI-state restoration returns from Canvas to `features/manuscript/view.js`, shared collection-rail helpers live in `core/collections.js`, Maps owns its Map/Line rail-label wrappers, and Cuts synchronization belongs to Durability. The reuse-before-create component contract from 10.3.8 remains authoritative.

This release marks the **frontend architecture freeze**: backend ownership, JavaScript ownership, CSS ownership/order, and the shared-component contract should not be reopened merely to reduce file size or pursue stylistic purity. Future refactors require a concrete responsibility or product problem. No schema, API, persistence, cloud/infra, or intentional visual behavior change is included.

## 10.3.8 — shared component contract and verifier failure attribution

10.3.8 adds an explicit reuse-before-create frontend component contract without reopening the completed ownership refactor. `static/css/shared/COMPONENTS.md` catalogs the canonical control class stacks and requires feature code to compose shared primitives with feature-specific hooks/modifiers instead of cloning presentation. The first ownership correction promotes the compact cross-workspace toolbar icon from the misleading historical `manuscript-tool-button` name to the neutral `tool-icon-button` primitive used by Manuscript, Canvas, Maps, Reviews, and Reviewer Mode.

The generic context-menu renderer also moves out of `features/manuscript/scenes.js` into `core/ui.js`; features continue to own their menu contents while shared UI owns menu rendering, positioning, nested submenus, disabled/danger states, and hover help. The verification heartbeat now distinguishes the most recently completed failing item (`last fail …`) from the oldest still-running job (`oldest …`). A rising failure count therefore no longer makes the active/oldest label look like the failing test. No backend, schema, storage, API, cloud/infra, or intentional visual behavior changes are included.

## 10.3.7 — CSS cascade cleanup

10.3.7 is the cleanup pass after the green 10.3.6 ownership checkpoint. The 22-file stylesheet boundary and its runtime order are unchanged. A conservative cascade audit removes 205 redundant declarations, including 24 rules that became empty, only when a later top-level rule with the exact same selector already reasserts the exact same property, value, and `!important` state. This removes 4,525 bytes of historical override debt without intentionally changing the final cascade. No broad dead-selector pruning, specificity rewrite, token substitution, visual redesign, schema/API/storage change, or backend change is included. The backend remains frozen at 10.2.27 and both application and cloud/infra repositories remain part of the release package.

## 10.3.6 — CSS ownership decomposition

10.3.6 begins the CSS architecture phase after the green 10.3.5 JavaScript checkpoint. The historical 3,445-line stylesheet is split into an ordered `static/css/` ownership tree while preserving the original rule sequence and effective cascade; `static/styles.css` is reduced to the global foundation owner rather than retained as a compatibility facade. CSS-aware source/browser contracts now read the complete ordered stylesheet boundary declared by `static/index.html`. No visual redesign, selector deduplication, schema/API/storage change, or backend change is intended. The backend remains frozen at 10.2.27 and both application and cloud/infra repositories remain part of the release package.

## 10.3.5 — frontend ownership cleanup

10.3.5 tightens the frontend ownership map without intentionally changing behavior. `core/ui.js` is reduced to shared UI infrastructure; common typography primitives move to `core/typography.js`; Manuscript bookmarks/view preferences, Reviewer view preferences, and Map view preferences now live with their owning features. Reviews and Projects are decomposed into package-owned modules, and Manuscript rendering is separated from Undo/Redo, workspace presentation, and save/persistence coordination. `static/app.js` remains a 13-line startup shim. The backend stays frozen at 10.2.27; CSS remains monolithic for the next frontend phase after this checkpoint is verified.

## 10.3.4 — frontend JavaScript ownership complete

10.3.4 finishes the build-step-free JavaScript decomposition. Split View, Manuscript imports, Checkpoints, History/Cuts durability UI, Notes, and Quick Open now have explicit owners under `static/js/`; `static/app.js` is reduced from 721 lines to a 13-line startup shim. Runtime JavaScript remains byte-for-byte equivalent to 10.3.3 before release-version normalization. The backend remains frozen at 10.2.27 and CSS is intentionally still monolithic for the next frontend phase.

## 10.3.3 — Maps and Metro frontend ownership

10.3.3 continues the build-step-free frontend decomposition. The Maps workspace now lives under `static/js/features/maps/workspace.js`, while the derived Metro View owns `features/maps/metro.js`. `static/app.js` shrinks from 1,477 to 721 lines and now begins at Split View. Runtime JavaScript remains byte-for-byte equivalent to 10.3.2 before release-version normalization; backend architecture, persistence schema/formats, and CSS architecture remain frozen.

## 10.3.2 — Tasks, linking, and Canvas frontend ownership

10.3.2 continues the build-step-free frontend decomposition from 10.3.1. Tasks now live in `static/js/features/tasks.js`, universal reference/linking behavior in `features/linking.js`, and Canvas browser behavior under `features/canvas/` split across hierarchy/UI state, nodes, and connectors. `static/app.js` is reduced from 2,579 to 1,477 lines and now starts at the Maps domain. Runtime JavaScript remains byte-for-byte equivalent to 10.3.1 after release-version normalization; backend architecture, persistence schema/formats, and CSS architecture are unchanged.

## 10.3.1 — boot, project shell, and Manuscript frontend ownership

10.3.1 continues the build-step-free frontend decomposition from the green 10.3.0 checkpoint. Crash recovery now lives in `static/js/core/recovery.js`; lease/bootstrap/refresh coordination lives in `core/boot.js`; Project Center/project settings/backup-recovery shell behavior lives in `features/projects.js`; and the Manuscript browser domain is split under `features/manuscript/` by navigation, rendering, editing, Scenes, annotation UI, and inline Find/Replace. `static/app.js` is reduced from 5,209 to 2,579 lines and now starts at the still-unextracted Tasks domain. Runtime source remains behavior-equivalent in script load order; no backend architecture, schema, persistence format, or CSS architecture is intentionally changed.

## 10.3.0 — frontend architecture begins

10.2.27 remains the frozen backend baseline. 10.3.0 starts the frontend decomposition without changing backend ownership or product behavior. The browser runtime now loads an ordered `static/js/` ownership tree for localization, transport/runtime primitives, shared state, shared UI, routing, and Reviews; `static/app.js` remains only the transitional remainder for feature domains not yet extracted. Historical frontend source contracts now read the complete runtime frontend boundary instead of requiring all JavaScript to live in one file. No bundler or build step is introduced.

## 10.2.27 — backend architecture freeze

10.2.27 is the final backend-organization pass before frontend refactoring. It fixes Project JSON inline-markup escape sentinel ownership and normalizes the final four flat HTTP domains (`annotations`, `comments`, `find_replace`, and `icon_markers`) into package-owned `routes.py` + `contracts.py` boundaries. The `src/api/` root now contains only API composition and truly shared request primitives; feature implementation lives under `src/api/<domain>/`. Cross-domain annotation helpers target `api.annotations.routes` explicitly. No schema, route, persistence-format, security-policy, or frontend behavior change is intended. Once this checkpoint is green, backend architecture is frozen and new backend work should follow the existing ownership rules rather than reopen the decomposition.

## 10.2.26 — final backend ownership cleanup

10.2.26 completes the backend ownership cleanup before frontend work: `api/workspace_tools.py`, `formats/project_json.py`, `workspace/projects.py`, and the global `backend/contracts.py` registry are removed. Workspace Tools, Project JSON, and Workspace Projects are explicit packages, while HTTP payload contracts now live beside their owning domains. No compatibility facades are retained. Route behavior, persistence schema, project/package formats, security policy, and frontend behavior are intentionally unchanged.

## 10.2.25 — Import/Export and Workspace Backup ownership

10.2.25 removes the mixed `api/import_export.py` module and replaces it with composition-only `api/import_export/`, separating DOCX parsing, bounded uploads/validation, DOCX import workflows, DOCX export, PDF export, and generic asset upload. It also replaces the 920-line `workspace/backups.py` service mixin with `workspace/backups/`, separating destination configuration, immutable authority/status, generation/retry state, publication/scrub bookkeeping, catch-up orchestration, and backup inventory/materialization. The `Workspace` service remains the public orchestration object. Route order, import/export formats, backup authority semantics, persistence schema, and frontend behavior are unchanged.

## 10.2.24 — Checkpoints domain ownership

10.2.24 removes the mixed `api/checkpoints.py` module and replaces it with composition-only `api/checkpoints/`, separating snapshot capture/storage, Chapter lineage, comparison, restore support, destructive manuscript restoration, Checkpoint lifecycle routes, and preflight/apply. Cross-domain History, Review Snapshot, and Import/Export callers now target the canonical Checkpoint owner directly. Route order, snapshot format, restore semantics, persistence schema, and frontend behavior are unchanged.

## 10.2.23 — Project Management HTTP ownership

10.2.23 removes the mixed `backend/project_routes.py` router. Platform Project Center HTTP ownership now lives under composition-only `api/project_management/`, split across profiles, workspace/identity state, metadata, destructive actions, project lifecycle, members, native package transfer, public JSON transfer, assets, and project health. `backend.core` still registers these routes in the same platform position, preserving route order and request-pipeline behavior. There is no compatibility facade and no schema, persistence-format, security-policy, or frontend behavior change.

## 10.2.22 — Durability HTTP domain decomposition

10.2.22 removes the mixed `api/history_backups.py` module. HTTP/domain ownership now lives under composition-only `api/durability/`, split across Cuts, immutable History, structural Undo, History baselines, Recovery, Backup Destinations, Recovery-backup scheduling, destination backup inventory/restore, and verified backup/integrity operations. Cross-feature calls now target those canonical subdomain owners directly. Route order and product behavior are intentionally preserved; there is no compatibility facade and no schema, persistence-format, security-policy, or frontend behavior change.

## 10.2.21 — Maps and Tasks domain decomposition

10.2.21 removes the flat Maps and Tasks API modules in favor of explicit domain packages. Maps now separates manuscript projection/anchors, Map lifecycle, Lines, Stops/Spans, Junctions, automatic layout, and Populate/source synchronization. Tasks now separates Task lifecycle, milestones, and duplication without introducing unnecessary abstraction. Cross-domain Map anchor detachment targets `maps.anchors` directly. Route order and product behavior are intentionally preserved. No schema, persistence format, security policy, or frontend behavior changes are introduced.

## 10.2.20 — Canvas release-contract synchronization

10.2.20 keeps the 10.2.19 Canvas decomposition unchanged and synchronizes verification metadata with the new package architecture. The Review implementation plan now tracks the current release, and the 10.1.0 API navigation contract recognizes `api/canvas/` as the canonical Canvas domain instead of requiring the deleted flat `canvas.py`. No production Python behavior changes in this corrective release.

## 10.2.19 — Canvas domain decomposition

10.2.19 removes the 1,214-line `src/api/canvas.py` monolith and replaces it with the `src/api/canvas/` domain package. Populate/templates, Developmental synchronization, Canvas registry/lifecycle, Structure Versions, Cards, Connectors, and Developmental manuscript apply now have explicit owners. The package boundary composes routers only; cross-feature callers target `canvas.developmental` or `canvas.populate` rather than a compatibility facade. The release also fixes the remaining 10.2.18 cross-domain source contract so Manuscript's Review dependency is asserted on its canonical `api.manuscript.document` owner.

## 10.2.18 — Manuscript domain decomposition

10.2.18 removes the 1,506-line `src/api/manuscript.py` monolith and replaces it with the `src/api/manuscript/` domain package. Navigation/bootstrap projections, document/basic Chapter metadata, image blocks, anchor repair, Chapter splitting/mutations, and Scene ownership now have explicit owners. The package boundary composes routers only; callers and tests target canonical owners rather than a compatibility facade. This release also fixes the two stale Review ownership contracts left by 10.2.17.

## 10.2.17 — Review domain decomposition

10.2.17 decomposes the 2,273-line `src/api/reviews.py` feature module into the `src/api/reviews/` domain package. Route behavior and ordering are preserved while lifecycle, frozen snapshots, portable `.breadreview` sessions, reader routes, questionnaires, feedback/suggestions, participant administration, markers, presets, and shared Review primitives now have explicit owners. The obsolete monolithic `reviews.py` path is removed rather than retained as a compatibility facade. Repository navigation/source-contract tooling now understands package-owned API domains.

## 10.2.16 — Complete composition source-contract cleanup

10.2.16 is a verification-contract-only corrective release. `test_v1011.py` now reads the application composition root through `tests/source_contracts.composition_source()` rather than reopening `src/app.py` directly, completing the canonical source-boundary rule introduced by 10.2.15. Production Python behavior, schema, HTTP routes, persistence formats, security policy, and frontend behavior are unchanged.

## 10.2.15 — Remove stale internal compatibility contracts

10.2.15 completes the pre-alpha cleanup started in 10.2.14 by migrating historical source-contract tests away from the deleted `storage/core.py` facade and the removed `app.py` namespace bridge. Historical persistence assertions now inspect the complete decomposed storage boundary through `tests/source_contracts.storage_source`, while application-composition contracts assert the explicit `_platform_app` + router installation model. No production runtime Python behavior, schema, HTTP route, persistence format, security policy, or frontend behavior is intentionally changed.

## 10.2.14 — Remove dead internal compatibility scaffolding

10.2.14 removes the temporary internal compatibility scaffolding left by the backend/storage refactors. `storage/core.py` is deleted; production code, current tests, and verification contracts import the focused persistence owner directly. `app.py` now exposes the FastAPI application instead of aliasing the entire backend composition module, and `backend.core` no longer re-exports Workspace, storage, project activation, configuration, or Canvas helpers for historical callers. Dead `_activate_project` / `_queue_remote_history` aliases are removed. No schema, HTTP route, persistence format, security policy, or frontend behavior is intentionally changed.

## 10.2.13 — Storage ownership decomposition

10.2.13 decomposes the former ~2,033-line `storage/core.py` persistence monolith into explicit owners for project context, database lifecycle, generation/CAS mutation coordination, immutable History, working-session Undo, assets, invariants, integrity diagnostics, and Recovery. Production modules no longer import `storage.core`; cross-storage calls resolve through module-qualified canonical owners so mutable state and monkeypatch boundaries do not split across copied bindings. The temporary compatibility facade is removed in 10.2.14 after production callers are migrated; 10.2.15 finishes migrating historical source-contract tests away from the deleted path. Storage schema 41, HTTP routes, project/package formats, security policy, and frontend behavior are intentionally unchanged.

## 10.2.12 — Workspace filesystem-owner corrective patch

10.2.12 keeps the 10.2.10 Workspace decomposition intact and fixes the final two historical removable-volume tests that still patched volume discovery through `workspace.backups`. Both volume metadata and mounted-volume discovery are now resolved through the canonical `workspace.filesystem` owner at call time. This preserves one authoritative patch/runtime boundary for platform filesystem behavior without reintroducing compatibility aliases into `workspace.backups` or `workspace/__init__.py`. No schema, HTTP route, persistence format, security policy, or frontend behavior is intentionally changed.

## 10.2.11 — Workspace ownership corrective patch

10.2.11 keeps the 10.2.10 Workspace decomposition intact and fixes three ownership-contract regressions found by the full verifier. Historical schema/provider source checks now inspect their canonical Workspace owners instead of `workspace/__init__.py`. Backup destination volume metadata is resolved through `workspace.filesystem` at call time, so monkeypatches and future platform-specific changes to the canonical filesystem owner are observed by backup naming/reclassification logic rather than being hidden behind a copied function binding. No schema, HTTP route, persistence format, security policy, or frontend behavior is intentionally changed.

## 10.2.10 — Workspace ownership decomposition

10.2.10 decomposes the 3,470-line `workspace/__init__.py` implementation into focused owners while preserving the single public `Workspace` object and all project/package/backup behavior. The package boundary is now lightweight; profiles, memberships, project/package lifecycle, Remote History, backup credentials/OAuth, backup destinations/publication, privacy erasure, providers, filesystem helpers, models, and constants each have an explicit module owner. Production consumers import internal helpers from those canonical owners rather than using the package namespace as a service locator. Historical source/monkeypatch contracts were updated to follow the moved owners. No schema, HTTP route, persistence format, security policy, or frontend behavior is intentionally changed.

## 10.2.9 — Thin-boundary corrective hardening

10.2.9 corrects the ownership seams exposed by the 10.2.8 `backend.core` decomposition without restoring a service locator. The outer ASGI server now reads hosted body limits, hosted guardrail helpers, and project-free runtime bootstrap metadata directly from their canonical owners instead of expecting those symbols on `backend.core`. Historical hosted/security tests patch `backend.environment.runtime` / `_HOSTED_LIMITS` directly. The canonical Current → local History observer is once again explicitly registered with storage from `backend.local_history`; 10.2.8 had moved the observer but accidentally omitted that registration, so automatic immutable History landmarks were no longer enqueued even though Current commits remained safe. No schema or persistence format changes are introduced.

## 10.2.8 — Thin backend composition boundary

10.2.8 finishes the `backend/core.py` decomposition without changing the product surface. `backend.core` is reduced from 1,534 lines to a 35-line FastAPI composition module. Local History publication, lifecycle/shutdown coordination, HTTP shell/security/error handling, editing leases, destructive confirmations, request binding/startup, workspace/project/profile routes, and runtime/session routes now have focused owners under `src/backend/`. Feature modules remain at zero imports from `backend.core`; route method/path/handler ordering is intentionally unchanged from 10.2.7. Historical tests that directly exercise lease, danger-confirmation, startup, or local-History internals now target the canonical owner rather than requiring those internals to be re-exported by the composition module. No schema, persistence format, security policy, or frontend behavior is intentionally changed.

## 10.2.7 — Canonical-owner verification cleanup

10.2.7 is a corrective verification release for the 10.2.6 ownership refactor. It does not restore feature symbols on `backend.core`. Historical tests that still referenced `ReviewSelectionIn`, `verified_full_backup`, or `_queue_destination_reconcile` through the composition module now import or patch the canonical owners (`backend.contracts`, `api.history_backups`, and `backend.recovery_runtime`) directly. The deterministic History-restore race test therefore reaches the intended stale-generation assertion instead of failing early on a removed compatibility attribute. No production runtime behavior, schema, HTTP route, persistence format, security policy, or frontend behavior is intentionally changed.

## 10.2.6 — Stateful backend ownership

10.2.6 removes the final feature-level dependency edges into `backend.core`. Portable Review process state now belongs to `backend.portable_review_runtime`; Recovery/backup coordinator state belongs to `backend.recovery_runtime`; Remote History coordination and explicit History landmarks belong to `backend.history_runtime`; and project switching belongs to `backend.project_activation`. Historical refactor-sensitive tests now patch those canonical owners directly, including hosted request-context security tests. Architecture and agent-navigation documentation is synchronized with the zero-`backend.core` feature dependency rule. `backend.core` remains a shrinking lifecycle/middleware/platform-HTTP assembly boundary for later decomposition. No schema, HTTP route, persistence format, security policy, or frontend behavior is intentionally changed.

## 10.2.5 — Explicit platform ownership

10.2.5 continues the backend-only refactor. The clean-home storage contract now uses the canonical `storage.core` owner. Feature modules no longer import general-purpose framework, standard-library, request-contract, content-validation, configuration, storage-row, or request-context symbols through `backend.core`; those dependencies live under explicit cohesive backend modules. Only stateful lifecycle/activation edges remain on `backend.core` for a later isolated move. No schema, HTTP, persistence, security, or frontend behavior is intentionally changed.

## 10.2.4 — Canonical package imports

10.2.4 corrects the 10.2.3 backend package-layout regression without restoring loose root modules or magic package re-exports. Callers now import moved implementations from their canonical owners (`storage.core`, `auth.sessions`, `security.*`, and `formats.breadreview`). The lightweight `storage` package boundary remains independently importable for schema consumers. No HTTP, schema, persistence, security, or frontend behavior is intentionally changed.

## 10.2.3 — Backend package ownership

10.2.3 is a semantics-preserving backend layout release. `src/` now contains only process entrypoints (`__init__.py`, `app.py`, `server.py`); former loose runtime modules live under explicit `auth/`, `security/`, `formats/`, `storage/`, `workspace/`, and `backend/` owners. Storage and workspace retain their historical import names as packages, and path-sensitive storage root resolution is preserved explicitly after the move. No HTTP, schema, persistence, security, or frontend behavior is intentionally changed.

## 10.2.2 — Canonical History monkeypatch contract

10.2.2 fixes the final verification regression from the explicit-dependency refactor. The History restore concurrency test now captures `api.history_backups.verified_full_backup` directly before monkeypatching that canonical owner, rather than capturing the lazy `app_module` compatibility alias and recursively resolving back into the patched function. Production behavior and the HTTP surface are unchanged.

## 10.2.1 — Explicit route type contracts

10.2.1 fixes the bridge-removal regression where extracted FastAPI route modules retained postponed annotations whose type names were no longer present in their owning module namespace. Every route now imports its request/typing contracts explicitly, and release verification resolves type hints for every registered endpoint so Pydantic/FastAPI cannot discover a missing annotation dependency only at request time. The HTTP surface and product behavior are otherwise unchanged.

## 10.2.0 — Explicit backend dependencies and composition root

10.2.0 removes the transitional 10.1 namespace bridge. Feature modules now use ordinary explicit imports from `backend.core`, and the small cyclic cross-feature helper graph is represented as named lazy function calls to canonical owners. `src/app.py` is reduced to a tiny idempotent composition entrypoint while the existing platform/lifecycle implementation moves intact to `src/backend/core.py`. A generated `src/api/dependency_manifest.json` makes feature dependency ownership machine-readable for contributors and coding agents. HTTP routes, schema, storage, security behavior, and frontend behavior are intentionally unchanged.

## 10.1.1 — Safe cross-domain bridge resolution

10.1.1 fixes the first runtime regression exposed by the 10.1 API decomposition. The transitional compatibility bridge is now two-phase: all domain modules load, their declared shared helpers are collected, then the complete helper namespace is rebound into every module before any request executes. This preserves the semantics-preserving refactor while supporting the mature cyclic helper relationships that existed inside the former monolith. No API, schema, storage, security, or frontend behavior is intentionally changed.

## 10.1.0 — Domain API decomposition and AI navigation

For architecture/navigation, start with [`AGENTS.md`](AGENTS.md); contributor setup is in [`CONTRIBUTING.md`](CONTRIBUTING.md).

10.1.0 is a semantics-preserving architecture release. Feature routes were extracted from the historical `src/app.py` monolith into domain-owned modules under `src/api/`; `src/app.py` is now the platform/composition root. The release adds `AGENTS.md`, a human route code map, a machine-readable route manifest, and a generated symbol/route index so contributors and coding agents can locate implementation code without scanning the repository. Historical source-contract tests now assert across the backend boundary instead of requiring all endpoints to remain in one file. The release verifier now discovers 10.x tests.

## 10.0.0 — Repository boundary

Breadmaker 10.0.0 establishes the permanent two-repository release layout. This folder is the public/open-source `breadmaker` repository. The commercial deployment/IaC repository is the sibling `breadmaker-cloud` folder in the release archive. The application runtime and security baseline are inherited unchanged from the fully verified 9.6.15 release; local Breadmaker remains accountless and offline-capable. See `docs/REPOSITORY_SPLIT.md`.

## 9.6.15 — Verification isolation cleanup

- Keeps the 9.6.13 security runtime and 9.6.14 product behavior unchanged.
- Synchronizes the local bootstrap fallback diagnostic with the current release identity.
- Makes the 9.6.13 historical security contract forward-compatible with later patch releases.
- Makes the destination-reconcile scheduler integration test reset rebuildable process-global coordinator state before driving the worker directly, eliminating cross-test scheduler contamination without changing production Recovery behavior.

## 9.6.14 — Verification contract synchronization

This patch contains no production behavior changes. It synchronizes the Review implementation-plan release marker and updates two brittle source-shape verification contracts to the current runtime architecture: `/api/runtime` bootstrap ordering is checked inside `local_origin_guard()` itself, and project import asserts the explicitly resolved owner identity passed to the runtime repository.

## 9.6.13 — Bounded final security review

This patch intentionally contains only three currently relevant corrections found in the final 9.6.12 audit: hosted project-administration endpoints require project.manage/Owner, expired opaque sessions prune their detached Portable Review package/passphrase state, and public hosted project records no longer expose globally stable owner_user_id values. Larger future hardening (for example hosted WebDAV SSRF defenses and OS-keychain integration) is tracked separately rather than being folded into this release.

## 9.6.12 — Medium/low security closure

- Makes project editor leases contention-fair without changing the single-writer model: an uncontended tab may renew indefinitely, but a waiting editor can take over after the current holder reaches a bounded continuous tenure. This prevents a forgotten or malicious project member from monopolizing editing forever.
- Binds destructive confirmation challenges to the exact actor, destructive action, and target project/installation. A confirmation generated for one project or account cannot authorize another target.
- Seals the complete local remote-backup credential record with AES-GCM in `backup_credentials.json`; WebDAV usernames/passwords, Google client secrets, and refresh tokens are no longer stored as readable JSON. The owner-only encryption key remains installation-local and is excluded from exports/packages. Pre-alpha plaintext credential stores are not migrated and require reconnecting the destination.
- Scopes Manuscript Review browser-local UI state by both user and project so two people sharing a browser/project do not inherit each other's panel/filter selection.
- Redacts globally stable `usr_…` account IDs from hosted project-member listings for ordinary Editors; project-local `member_…` identities remain available. Member managers retain the account ID needed to administer membership.
- Enforces the existing hosted single-worker requirement with an exclusive workspace process lock and rejects configured worker counts greater than one until sessions/leases/coordinators move to a shared store.
- Refuses legacy `.doc` conversion in hosted mode. Local desktop conversion remains available when LibreOffice is installed, using an isolated temporary LibreOffice profile/home. The hosted Docker image now runs as an unprivileged `breadmaker` user.
- Adds `test_v9612.py` security regressions and updates the historical Google credential-store contract for sealed-at-rest records. Project schema remains 41.

## 9.6.11 — Hosted HIGH security hardening

- Closes imported external-resource tracking: project-package Manuscript images, frozen Review images, Canvas images, Cuts, and portable `.breadreview` snapshot images must reference canonical Breadmaker-owned same-origin assets. The browser independently refuses unknown image URLs, and CSP restricts images to same-origin/data/blob sources.
- Makes unauthenticated `hosted_dev` a hard startup error whenever `BREADMAKER_ENV=production`, even if the explicit development opt-in flag is accidentally present. Test authentication retains its existing production hard-stop.
- Adds hosted-only abuse controls without changing local desktop behavior: per-owner project count and storage quotas, per-project storage ceilings, upload/document expansion limits, per-account write/heavy-operation rate buckets, and request-body ceilings. Trashed projects still count toward owner storage so Trash cannot be used to evade quota.
- Enforces hosted body limits at the outer ASGI boundary as bytes arrive, covering chunked requests that omit `Content-Length` before multipart/JSON parsers can spool unbounded input. The hosted container now launches `src.server:app` so this boundary is actually present in the packaged deployment path.
- Adds browser hardening headers: nonce-free hashed-script CSP, frame denial, MIME sniffing protection, no-referrer, restricted browser permissions, and HSTS on HTTPS hosted requests.
- Repairs the remaining 9.6.10 verifier contracts for Map cache refresh, Canvas nonblocking viewport persistence, and runtime bootstrap source shape. Project schema remains 41.

## 9.6.10 — Startup projection and verification stabilization

- Removes book-size chapter revision hashing from normal bootstrap. Startup now builds Scene navigation and gap metadata from one lightweight manuscript projection; exact Chapter revisions are hydrated only when an editing context or crash-draft comparison needs them.
- Starts locale/runtime discovery together, then profile/portable-review/workspace discovery together, and finally overlaps the editor lease with bootstrap, collaborator UI-state restoration, and crash-journal lookup. Security/lease gates still resolve before an editable workspace is exposed.
- Keeps Full Text navigation cached for instant revisits but invalidates that cache on explicit manuscript structural reloads, fixing stale image insert/copy/move rendering after the 9.6.6 performance work.
- Repairs release contracts that still asserted pre-performance source shapes, including Canvas viewport persistence, runtime bootstrap, WAL setup, launcher imports, request boot ordering, and asynchronous writer-idle History.
- Removes verifier-only timing races in the Recovery scheduler compatibility test, the synthetic profile reload test, and the durable Canvas/Tasks/Maps workflow by waiting on observable state rather than fixed sleeps.
- Preserves the 9.6.3+ CRITICAL tenant-isolation/XSS boundaries and the 9.6.6 interactive performance architecture. Project schema remains 41.

## 9.6.9 — Package-qualified bootstrap correction

- The desktop launcher now starts `src.server:app`, and `src/server.py` imports the Breadmaker backend with the package-relative `from . import app as core`. This removes the ambiguous top-level `import app` that could resolve to a different or partially initialized module under the real Uvicorn/Python environment.
- The outer runtime bootstrap no longer falls back to `core.runtime`; it requires the canonical `_runtime_bootstrap_payload()` helper. Missing bootstrap support therefore fails explicitly instead of dereferencing an unrelated module shape.
- Local desktop startup may recover from either a 404 or a wrapper-level 500 from `/api/runtime`, but only after `/api/health` attests `app == Breadmaker Editor` and `runtime == local`. Hosted/account runtimes still fail closed.
- Historical 9.6.7/9.6.8 release tests now express minimum-version contracts so later corrective patches do not fail merely because the version advanced.
- 9.6.9 intentionally leaves the 9.6.6 performance hot-path work and the CRITICAL security boundaries unchanged.

## 9.6.8 — Resilient local bootstrap boundary

- The desktop launcher now runs a tiny outer ASGI bootstrap wrapper (`server:app`) that owns `GET/HEAD /api/runtime` before the main FastAPI application, middleware, project resolution, or Recovery code can see the request.
- Local browser startup has a second fail-safe: if `/api/runtime` still returns 404, `/api/health` must attest `app=Breadmaker Editor` and `runtime=local` before the frontend reconstructs the fixed local capability set and continues booting. Hosted/account runtimes still fail closed; there is no authentication downgrade fallback.
- The fallback records frontend/backend version information in the browser console so mixed installations are diagnosable instead of being misreported as a corrupt project.
- 9.6.8 intentionally leaves the 9.6.6 performance hot-path changes and 9.6.3 critical security boundaries unchanged while startup is being stabilized.

## 9.6.7 — Project-free bootstrap hardening

- `/api/runtime` is now answered before project/profile/request-context resolution. Runtime discovery cannot be turned into a project 404 by damaged, stale, unsupported, or partially initialized local state.
- The normal `/api/runtime` route remains and delegates to the same project-free payload helper so direct dispatch and HTTP middleware cannot drift.
- Adds a release regression contract that requires runtime bootstrap to stay ahead of project/user binding.

## 9.6.6 — Interactive hot-path performance restoration

- Restores latency-sensitive reads to true read-only WAL connections instead of routing GET endpoints through `BEGIN IMMEDIATE`; persistent WAL mode is configured once during project initialization rather than renegotiated on every SQLite connection.
- Makes Full Text a single-pass read model: blocks are fetched once, Chapter revisions are derived from those rows, annotation lookup no longer expands thousands of block IDs, and the active Book is prefetched only after Manuscript is interactive.
- Removes synchronous immutable-History capture from Canvas Create/Populate. Canonical Current remains immediately durable; automatic immutable History is debounced to writer-idle time and still flushes on shutdown.
- Binds Canvas/Map Session Undo snapshots to the concrete Canvas/Map being edited. Canvas creation uses a bounded Canvas-only scope and Map creation snapshots only Map metadata.
- Replaces Canvas Populate's per-card position PATCH fan-out with one bounded batch mutation.
- Caches Story Map manuscript chronology by manuscript-specific update signatures, removes duplicate Map lookup and anchor-label N+1 queries, and serves Map detail through concurrent read connections.
- Keeps 9.6.3+ critical hosted-security boundaries intact. Project schema remains 41.

## 9.6.5 — Performance recovery and critical-security stabilization

- Startup hydration is route-aware. Breadmaker batches collaborator UI preferences into one request, applies them before the first meaningful workspace paint, and loads only the active heavy workspace instead of eagerly opening Manuscript and Canvas data on every reload. This removes the default-state → restored-state flash and cuts boot I/O.
- Canvas and Story Map switching no longer blocks on persisting the workspace being left. Story Map detail payloads are cached, in-flight requests are deduplicated, and off-screen expanded Map details are deferred to idle time.
- Structural Undo gains explicit bounded scopes for Canvas and Maps. Populate operations snapshot only the tables they can mutate instead of serializing the whole project before and after the action; unknown/cross-domain operations retain the conservative full snapshot.
- Package XSS validation keeps the 9.6.3 security policy but uses a streaming capability validator at import/Recovery trust boundaries instead of allocating a BeautifulSoup DOM per stored prose fragment. Normal editor sanitization remains unchanged.
- The 10,000-paragraph scale contract keeps the hard 10-second per-test watchdog but runs in a dedicated no-contention verifier lane rather than competing with the parallel core suite.
- Local Recovery worker coordination keeps the legacy selected-project scheduler alias synchronized while preserving per-project scheduler state, closing the destination-retry regression without reintroducing process-global hosted routing.
- The top-right profile control is protected from transient boot mode classes and flex shrink while the workspace hydrates.
- Adds 9.6.5 regression contracts for boot hydration, batched UI state, scoped Undo, Canvas/Map hot paths, package-validation implementation boundaries, scale verification, and profile chrome. Project schema remains 41.

## 9.6.4 — Critical-security corrective verification pass

- Kept session-isolated Portable Review state and explicit authenticated project binding from 9.6.3.
- Relaxed package validation from serializer byte equality to executable-capability validation so harmless current-schema historical wrappers remain readable while active HTML remains rejected.
- Fixed project activation ordering so a newly created project is not published as active before its SQLite database is initialized and validated.
- Restored release cache-buster consistency, corrected stale 9.x verification assertions, and made Milestone link refresh deterministic.
- Expanded release verification continued to include 9.x contracts; the dedicated 9.6.3 critical-security suite remained part of the gate. Project schema remains 41.

## 9.6.3 — Critical hosted security boundaries

- Temporary `.breadreview` state is no longer process-global. Package path, temporary directory, dirty state, filename, and unlocked passphrase are isolated by opaque server session ID in authenticated hosted mode; local Breadmaker retains one local slot. Logout erases the current session's temporary package and passphrase.
- Standalone `BREADMAKER_PORTABLE_REVIEW` mode now fails closed when account sessions are enabled, preventing a process-global portable package from becoming shared authenticated state.
- `/api/portable-review/import-current` explicitly resolves the authenticated account's current project, verifies the package project identity, binds that project + actor, requires `review.process`, and only then opens the write transaction. It can no longer fall back to process-global storage.
- `.breadmaker` verification independently rejects executable or out-of-policy HTML in manuscript blocks, frozen Review blocks, and Cuts before import. Harmless historical wrappers remain readable; package hashes and canonical-state hashes are treated as integrity checks, not HTML trust.
- `.breadreview` manifest validation independently rejects executable or out-of-policy frozen manuscript HTML before Reader Mode can render it.
- Adds adversarial 9.6.3 regression coverage for cross-session portable Review isolation and attacker-crafted packages whose integrity hashes have been correctly recomputed. Project schema remains 41.

## 9.6.1 — Account-session isolation review

- Hosted account sessions now fail closed on missing/corrupt project membership sidecars instead of applying the local-installation reclaim rule. Project bytes remain untouched for deliberate operator recovery.
- Account requests with no explicit project resolve only inside the authenticated account's memberships; process-global shell selection is never an account routing fallback. Background History/Recovery startup and shutdown work likewise ignores process-global selection under account sessions.
- Test-auth fixtures use a dedicated ID registry under a lock rather than display-name matching. The `unowned_user` fixture rotates to a fresh identity if prior test mutations gave it project access.
- Auth session/logout are project-context-free, logout survives an already-expired editing session, and authenticated API responses/errors are `private, no-store`.
- Browser crash drafts are scoped by user + project + chapter. Project UI state is namespaced by project-scoped `member_…` identity so collaborators do not overwrite each other's local navigation/preferences.
- Account-session browsers no longer transport `usr_…` identity in headers/query strings; the HttpOnly session cookie is the only hosted identity authority. Failed import/project-switch rollback and backup helpers no longer consult another account's process-global project.
- Project schema remains 41. These are hosted identity/runtime isolation corrections only.

## 9.6.0 — Server session + deterministic test-auth foundation

- Adds opaque server-side sessions as the common authentication handoff for future Auth0/OIDC and automated tests. Browser cookies contain only a 256-bit random token; Breadmaker stores only its SHA-256 digest and enforces idle + absolute expiry.
- Adds hosted `BREADMAKER_AUTH_MODE=session`, which is authenticated-by-default and has no developer-identity fallback. The future Auth0 callback only needs to resolve/create a Breadmaker user and issue one of these sessions.
- Adds `BREADMAKER_AUTH_MODE=test` for automation. It requires `BREADMAKER_ENV=test` plus a dedicated `BREADMAKER_TEST_AUTH_SECRET` of at least 32 characters; production + test auth is a hard startup failure.
- Test authentication can bootstrap only predefined owner/editor/second-owner/unowned fixtures. The bootstrap secret never authorizes application APIs directly; it only creates the same HttpOnly session used by the normal hosted path.
- Adds `/api/auth/session` and real session logout. Hosted session profiles are account-shaped while the local free runtime keeps its existing local profiles and account-optional behavior.
- Project schema remains 41. Auth/session state is server/runtime infrastructure, not portable project content.
- Installation-wide Export All / Delete All are disabled for account sessions until account-scoped export/erasure exists; project-level export/delete remains membership-authorized.

## 9.5.0 — First hosted persistence runtime

Breadmaker now ships two runtime targets from the same repository: `local` and the developer-only `hosted_dev`. The hosted target uses the same Schema-41 project engine against a server-owned persistent data volume, ignores browser-supplied user identities, hides local-filesystem/profile-switching capabilities, enforces same-origin browser mutations, and refuses to boot unless the operator explicitly opts into its intentionally unauthenticated development identity. `./run-hosted-dev` and `deploy/Dockerfile.hosted-dev` exercise the same frontend/domain code without creating a second Breadmaker application. This is a hosted persistence prototype, not the public paid service; account authentication remains the next boundary.

## 9.4.1 — Request-scoped runtime corrective pass

- Recovery coordination now treats clean inactive project schedulers as rebuildable memory instead of permanent background work.
- Fresh application lifespans reset only ephemeral Recovery scheduler/retry state; durable protection continues to come from each project's verified `recovery_state.json`.
- Shutdown/worker work therefore scales with the selected project plus projects that are actually dirty, retrying, or awaiting destination reconciliation.
- The legacy History checkpoint switch contract now verifies the 9.4 invariant: a request stays bound to its source project even if the local shell selects another project mid-request, so no cross-project checkpoint can be created.
- No project schema change. Schema remains 41.

## 9.4.0 — Request-scoped project runtime

- Binds every project API request to an explicit project + actor context instead of using the process-global selected project/profile as storage or authorization authority. The local shell may still select one current book, but concurrent request work no longer follows that mutable selection.
- Makes SQLite paths, assets, local History, Review actor attribution, and project authorization resolve from the request/task context. Raw image/download URLs carry the same project/actor identity because browser resource requests cannot attach the normal API headers.
- Scopes editor leases and structural Undo isolation by project so one local session switching books cannot invalidate or redirect unrelated project work.
- Gives each tracked project its own Recovery scheduler and destination reconciliation state; automatic and shutdown Recovery follows the project that actually changed rather than whichever project is selected in the shell.
- Rebinds create/import-as-new-project workflows before writing any content and carries the requesting local profile as the new project owner, preventing a mid-request project switch from writing into the source project.
- Keeps the local profile selector intentionally non-authenticating. The request actor header is a local identity seam; a hosted runtime must replace it with server-authenticated identity rather than trusting client-supplied account IDs.
- Project schema remains 41 and portable `.breadmaker` data is unchanged.
- Retains a separate OS process lock for every project first touched by a request, and resolves Workspace/Recovery state before claiming the browser editor lease so corrupt projects still reach Recovery safely.

## 9.3.1 — Runtime boundary hardening

- Binds staged image uploads to the project asset root where they started; a project switch during an asynchronous upload now fails closed instead of allowing the staged file to finalize into another project.
- Makes frontend runtime discovery fail closed and cross-checks the runtime descriptor returned with Workspace state rather than silently assuming a local runtime if capability discovery fails.
- Applies runtime capabilities on both sides of Backup Destination UI/API boundaries: native folder choices require `native_filesystem`, and direct Google Drive/WebDAV connections require `direct_remote_backups`.
- Stops exposing the local workspace filesystem root from `/api/workspace` when the runtime does not advertise native filesystem access.
- Documents the remaining hosted prerequisite explicitly: current manuscript/database access is still process-bound to one active project and must become request/session scoped before a multi-user hosted runtime is introduced.

## 9.3.0 — Runtime infrastructure boundary

- Adds one runtime service container for environment-specific infrastructure: Identity, Authorization, Projects, Assets, History, Backups, and Capabilities.
- Ships only the local runtime in 9.3.0. Existing SQLite/project-folder behavior remains the implementation underneath the new contracts; no cloud persistence or authentication is introduced yet.
- Routes profile/project operations, project asset reads/writes, History replication/retention, and Backup Destination operations through the runtime services instead of making feature/API code depend directly on the local workspace implementation.
- Adds `/api/runtime` plus a frontend capability seam so future hosted-only/local-only UI differences can be expressed as capabilities instead of `desktop` / `web` feature forks.
- Unknown runtime modes fail closed. Project schema remains 41 and portable `.breadmaker` data is unchanged.

## 9.2.1 — Membership recovery hardening

- Treats project `local.json` membership data as the non-portable installation sidecar it is: a valid project copied into a different Breadmaker home is reclaimed for a valid local profile instead of being rejected because its old `usr_…` ids do not exist there.
- Preserves unambiguous valid memberships when repairing a damaged/foreign sidecar; falls back to the current valid local profile when no valid owner survives.
- Updates the legacy Review accept-refresh contract to the canonical manuscript reload path introduced in 9.0.1.
- No project schema change; schema remains 41.

## 9.2.0 — Local membership and actor identity

- Replaces owner-only project visibility with installation-local project memberships.
- Every project has one Owner and can also have Editor members, each linked to a local profile through a stable project-scoped `member_…` identity.
- Switching profiles now filters projects by membership; shared Editor projects can appear for more than one profile while still preserving each profile’s independent project list and last-opened project.
- Adds one identity/authorization service with named permissions for project read/edit/manage/delete, membership management, Review processing, Tasks, and Checkpoints.
- Direct project activation/export/rename/delete and membership administration now enforce that authorization seam instead of trusting UI visibility.
- Writer-side Review events persist a portable actor snapshot without persisting machine-local `usr_…` account IDs. Review Participants remain separate identities.
- Membership stays in non-portable `local.json`; `.breadmaker` packages remain installation-identity neutral. Project schema remains 41.

## 9.1.0 — Local multi-profile ownership

- Adds multiple installation-local profiles with one active profile at a time. The avatar menu can create and switch profiles while keeping the phase-one Log out action intentionally unwired.
- Gives each local profile its own project list. Creating, importing, and restoring projects associates them with the active profile, and switching profiles returns to that profile’s most recently opened project.
- Stores project ownership in a non-portable local sidecar so registry recovery can reconstruct profile/project relationships without putting local usr_ identities into `.breadmaker` exports.
- Keeps project schema 41 unchanged; profiles are an installation/workspace concern rather than manuscript/project content.

## 9.0.1 — Profile/review release hardening

- Synchronizes release/cache-buster contracts for the 9.x profile foundation.
- Keeps the name-seeded local profile avatar contract aligned with the clickable profile control.
- Refreshes canonical manuscript state after accepting a Review suggestion from writer Reviewer Mode.
- Updates Review verification contracts to the non-invasive CSS Custom Highlight rendering architecture.

## 9.0.0 — Local profile foundation

- Replaces the anonymous top-right avatar with an installation-local profile that persists across Breadmaker projects without becoming part of portable `.breadmaker` project packages.
- Adds editable profile name and profile picture. Imported pictures are normalized in the browser to WebP at a maximum dimension of 512 × 512 before Breadmaker stores them.
- Adds deterministic GitHub-style fallback identity art built from mirrored line segments. The normalized profile name seeds both the line pattern and a paired high-contrast background/line palette, so the same name produces the same avatar.
- Adds a compact top-right profile menu with Edit profile and the intentionally unwired phase-one Log out action.
- Keeps project schema 41 unchanged because profile/account identity is installation state rather than project content. Delete All Data still erases the profile with the rest of Breadmaker-owned local data.

## 8.20.12 — Review status + instant Manuscript round opening

- Shortens the Start Review confirmation to the two durable effects that matter: reviewers see a frozen round, and permanent Checkpoints are created for it.
- Adds compact state glyphs to the Reviews rail only for active work: in progress, paused, or reviewing feedback.
- Removes the forced Manuscript Review “Loading…” screen, opens the round shell immediately, fetches the selected round in one request, and caches opened rounds for instant repeat navigation.

## 8.20.11 — Review UI consistency pass

- Aligns Review feedback overflow menus to the top-right, gives Canvas/Maps/Reviews collection add controls more breathing room, and corrects Review empty-state wording/actions.
- Brings Review Marker empty states and marker-copy into parity with Manuscript Markers, replaces misleading selected-row treatment with a quiet attached indicator, and improves marker-list spacing.
- Makes questionnaire Attach/Attached state update immediately, removes the participant-name example default, and updates the affected Review copy across all seven locales.

## 8.20.10 — Incremental Review annotation repaint

Manuscript Review annotations now invalidate and recalculate only feedback touching the edited block during typing. Unchanged feedback ranges are cached and reused, logical block text is read once per repaint, and the typing repaint debounce is 140 ms. Structural manuscript/view changes still force a full conservative invalidation. Review decorations remain outside the editable manuscript DOM.

## 8.20.9 — Resilient live Review comment anchors

- Manuscript Review Comments and Highlights now derive a live current-text anchor from the immutable Review snapshot instead of requiring the frozen selected text to remain byte-for-byte unchanged.
- Ordinary edits inside or around a commented passage keep the feedback attached while the writer addresses it. The original Review anchor remains immutable for history/audit.
- Suggestions remain strict: their source span must still match before Breadmaker presents/applies the proposed edit.

## 8.20.7 — Manuscript Review hover and comment interaction polish

- Manuscript Review detail now shows only open/actionable feedback in the main working list.
- Resolved comments/highlights and accepted, rejected, or task-created suggestions move into a collapsed Completed section instead of remaining mixed with active work.
- Completed feedback no longer paints inline manuscript annotations. Reopening a resolvable item moves it back into active work.
- Accepting a suggestion reloads the live manuscript immediately, so deletes disappear and replacements/inserts become real manuscript text as soon as they are accepted.

## 8.20.3 — Manuscript Review continuity + clearer replacement suggestions

- Suggested deletes/replacements/inserts use the manuscript itself as the source of truth instead of repeating selected text in hover cards.
- Open suggestions expose Accept / Reject directly in the hover card; Create Task remains under the ellipsis menu.
- Manuscript Review context menus render above hover cards.
- Left-side Review gutter indicators no longer overlap single-item counts, and selected feedback is expressed in the manuscript/panel rather than by selecting a count badge.

## 8.20.0 — Reviews in Manuscript

- Manuscript Reviews is now a working review layer with an Open / All / Closed round list.
- Selecting a round opens a compact detail view with reviewer filtering, manuscript-order feedback navigation, and synchronized manuscript annotations.
- Review rounds can be closed or reopened from Manuscript using the existing immutable round lifecycle.
- Questionnaire answers and general feedback open in a focused modal and can create Tasks through the existing Review feedback workflow.
- Inline Review feedback can be resolved/reopened or converted to Tasks without leaving Manuscript.
- Original Review anchors are only painted when the current manuscript text still matches exactly; changed or missing passages are never guessed.
- The full Reviews workspace remains the place for administration, with an explicit Open in Reviews action from Manuscript.

## 8.19.13 — Image resize commit safety

- Manuscript image and image-row size sliders now preview continuously while dragging but mark the manuscript dirty only once when the resize interaction is committed.
- Releasing the range control (or leaving it after keyboard adjustment) creates one persistence boundary instead of repeatedly scheduling autosaves during the drag.

## 8.19.12 — Constrained manuscript image rows

- Drag an image beside another image to form a simple 2–4 image row; drag it above/below prose or another row to separate it again.
- One Size slider controls the width of the entire row. Images inside the row share that space equally; there are no per-column layout controls.
- Paste remains vertical: pasting while an image in a row is selected inserts below the entire row rather than joining it.
- Image drag/drop now snaps to legal boundaries instead of implying that arbitrary positions inside prose are valid drop targets.
- Image-row relationships persist through Cuts, Checkpoints, History, Reviews, Split View, DOCX/PDF export, undo/redo, and project storage.
- Bumps the pre-alpha schema to 41; old schemas remain intentionally unsupported rather than migrated.

## 8.19.11 — Manuscript image completion parity

- DOCX and PDF exports now respect each image occurrence's 5–100% manuscript-column width and visible Footer; DOCX also writes the accessibility Description into the exported image metadata.
- The 0.5-second image Description hover is shared by editable Manuscript images and read-only manuscript images in Split View, Reviewer Mode, Checkpoint/History comparison, and Cut Details.
- Dragging an image or image file into the blank manuscript area after the final block now shows the black insertion line at the true end and inserts there.

## 8.19.10 — Manuscript image interaction polish

- Image insertion anchors now survive a save that converts a temporary manuscript block ID into its permanent ID, fixing paste/add failures from a valid caret in newly edited prose.
- Image drag/drop shows a clear black insertion line; existing image blocks can be moved into prose at an exact split offset as well as between blocks.
- Image width now applies only to the image itself. Footer text has an independent readable centered measure across Manuscript, Split View, Reviews, Checkpoint comparison, and Cut Details.
- Backspace/Delete removes a selected image block directly. Backspace/Delete adjacent to an image removes the image without changing the neighboring paragraph style or indentation.
- Hover Description is positioned above the image.

## 8.19.9 — Manuscript tooltips describe the tools

- Rewrites the Manuscript Image tooltip to explain adding, arranging, sizing, and describing images rather than the side-panel mechanism.
- Rewrites the Manuscript Reviews tooltip to explain Review feedback in manuscript context rather than the side-panel mechanism.
- Keeps Notes, Markers, Checkpoints, Cuts, and Editor’s Eye wording unchanged because those already describe user purpose consistently.
- Seven locales remain synchronized.

## 8.19.8 — Cross-tab Manuscript Split View

- Chapter and Scene Split View targets now expose their own Tab selector instead of being restricted to the editable Manuscript tab.
- Adds Scene as a first-class Split View target.
- Adds Full Tab as a Split View target, rendering the complete chosen Tab continuously in chapter order without navigating the editable Manuscript.
- Full Tab/Scene rendering reuses the same read-only manuscript block renderer, including first-class image width, Footer, and accessibility Description behavior.

## 8.19.7 — Image comparison parity

- Changes Image-panel prompts to Breadmaker-style questions: “What should appear below the image?” and “What does the image show?”
- Checkpoint and History comparisons now render real image blocks with their saved width, Footer, and accessibility Description instead of `[Image]` placeholders.
- Image occurrence changes (asset, size, Footer, or Description) now participate in Checkpoint/History diff identity.
- History comparison serves image bytes from the verified immutable History revision when the old asset no longer exists in Current.

## 8.19.6 — Manuscript image cut/paste metadata integrity

Image Cut/Copy now snapshots the current inspector values for Size, Footer, and Description before moving an image block, ensuring those occurrence properties survive Ctrl/Cmd+X → Ctrl/Cmd+V even when they were just edited.

## 8.19.5 — Manuscript image clipboard polish

- Image Footer and Description fields now follow the shared `(optional)` label convention and use purposeful placeholder prompts.
- Selected manuscript image blocks support Copy, Cut, and Paste with Ctrl/Cmd+C, Ctrl/Cmd+X, and Ctrl/Cmd+V.
- Image clipboard operations preserve the shared asset reference, width, Footer, and Description without duplicating image bytes.

## 8.19.4 — Verification contract maintenance

- Updated stale Manuscript toolbar, bookmark-localization, and Image-panel test expectations after the 8.19.x UI changes.
- Preserved the 200-operation structural soak and 10,000-paragraph History scale contracts while removing redundant expensive checks that consumed per-item timeout headroom.
- Hardened the durable selection helper against a Playwright visibility race without changing application behavior.
- `verify_release.py` is unchanged; full verification remains intended to be run by the release operator.

## 8.19.3 — First-class Manuscript images

- Adds first-class atomic Manuscript image blocks with a 5–100% text-width slider, visible optional Footer, hidden optional accessibility Description, and 500 ms description hover.
- Adds images through the side-panel file picker, clipboard paste, or file drag/drop into manuscript text; existing manuscript images can be dragged between blocks.
- Preserves transparent/source image bytes without transcoding and accepts PNG, JPEG, WebP, GIF, AVIF, and BMP up to the existing safe upload limit.
- Uses content-addressed project image files so identical image bytes are reused across manual uploads, Canvas, DOCX imports, and Cuts imports.
- Persists image presentation metadata through Manuscript saves, Cuts, Checkpoints, Review snapshots, split views, and project durability/integrity boundaries.
- Bumps the pre-alpha project schema to 40; old schemas remain intentionally unsupported rather than migrated.

## 8.19.2 — Bookmark navigation and Manuscript toolbar naming

- Left Manuscript navigator bookmark breadcrumbs now jump to the saved bookmark instead of arming/removing it.
- Right-clicking the quiet top-left viewport bookmark exposes an explicit Remove Bookmark action.
- Moves the viewport bookmark tighter into the top-left corner.
- Renames the Manuscript Markup surface to Markers without changing internal IDs, API fields, or storage semantics.
- Places Reviews after Checkpoints and before Cuts in the Manuscript toolbar.
- Schema remains 39.

## 8.19.1 — Bookmark navigation polish

- Moves the viewport bookmark closer to the Manuscript viewport corner.
- Makes its delayed tooltip navigation-specific and identifies the bookmarked Chapter, Scene, or passage when possible.
- Leaves bookmark creation/explanation tooltips unchanged.

## 8.19.0 — Manuscript toolbar breathing room

- Moves Find and replace into the View menu directly below Show Scenes while preserving the existing search/replace popover and shortcut.
- Moves the project bookmark jump control to a quiet top-left manuscript viewport indicator that appears only when a bookmark exists.
- Adds Bookmark selection to the selected-text right-click menu and a 300 ms explanatory tooltip to the compact selection bookmark action.
- Adds Image and Reviews manuscript toolbar entry points with matching empty side panels and tooltips; feature behavior is intentionally not implemented yet.
- Schema remains 39.

## 8.18.27 — Explicit browser boot-ready boundary

- Exposes an inert boot-completion promise/marker for deterministic browser automation readiness.
- Fixes the marquee browser contract to activate Canvas only after completed application boot.
- Repairs the 8.18.25 retry source contract so it asserts behavior rather than docstring formatting.

## 8.18.26 — Browser boot barrier and reliability shard headroom

- Current-browser helpers now wait for Breadmaker boot to complete its final route/view restoration before returning a page to a test, eliminating races where late boot work could hide the workspace a test had just opened.
- Randomized structural reliability keeps 200 total mutations while using ten 20-operation shards instead of eight 25-operation shards, preserving the 10-second Core/API limit with more margin.
- The 8.18.25 retry release contract now checks Durable E2E exclusion case-insensitively instead of depending on docstring capitalization.
- No Breadmaker feature behavior or schema changes.

## 8.18.25 — Transparent browser retry and deterministic Canvas marquee setup

Release verification now retries an initially failed current-browser item exactly once in a fresh pytest process at the end of the browser stage. Recovered flakes remain visible in console/report accounting; a second failure stays red. The Canvas marquee/autoscroll contract now selects its prepared Canvas through Breadmaker's `selectCanvas` boundary instead of racing Canvas-rail rendering that belongs to other tests.

## 8.18.24 — Unit contract alignment and focused Canvas marquee activation

- Corrects the new Unit layer to match established History retention semantics: `None` resolves to the configured/default retention policy rather than implying `forever`.
- Treats `./relative` BreadReview archive members as safe normalized relative names; traversal, absolute, NUL, and backslash paths remain rejected.
- Keeps the v6.4 Canvas marquee/autoscroll contract focused on marquee behavior by activating the Canvas workspace deterministically instead of re-testing top-level navigation.
- No product behavior or schema change.

## 8.18.23 — First-class unit verification gate

- Added a dedicated `tests/unit/` layer for fast deterministic validation of retention, publication manifests, history policy, project JSON validation, BreadReview safety primitives, and deterministic stage coordination.
- `./verify-release` now executes Unit first, then Core/API, Current Browser, and Durable E2E, and reports item accounting for each layer separately.
- Unit items have a strict 3-second per-item budget and do not eagerly boot the FastAPI application graph. Existing Core/API (10s) and Browser/E2E (15s) budgets are unchanged.
- The unit layer is included in QUICK and FULL verification modes; existing integration/browser coverage is retained rather than reclassified away.

## 8.18.22 — Browser timeout headroom and focused Maps creation contracts

- Browser and durable E2E pytest items now receive a hard 15-second setup + call + teardown budget; core/API tests remain at 10 seconds.
- Split the oversized v6.4 Station + Span creation browser workflow into focused Station and Span contracts.
- Removed fixed sleeps from both v6.4 creation contracts in favor of active-view, modal, inspector, and persistence completion signals.
- Added stable `data-testid` hooks for Map Line actions and Add Station/Add Span commands.
- Schema remains 39.

## 8.18.21 — Durable H1 actionability isolation

- Keep the H1 historical workflow focused on selection semantics and Chapter conversion rather than duplicating Playwright visibility/actionability coverage.
- Trigger the already-validated selection Style and Chapter menu handlers through DOM click dispatch, while dedicated toolbar tests continue to own visible-click behavior.
- Schema remains 39.

## 8.18.20 — Semantic test repair and deterministic interaction contracts

- Corrected the v7.7.8 remote-loss soak so it republishes the exact already-published revision whose remote snapshot was removed; a newer unpublished revision is correctly treated as a fresh publication, not a repair.
- Replaced headless double-click selection in the durable H1 workflow with a deterministic DOM Range followed by Breadmaker's real pointer-release selection boundary.
- Reworked the durable Span endpoint drag to target an exact logical Line position using the current Map layout plan and SVG transform instead of guessing from rendered route segmentation.
- Added release contracts and testing guidance for repair semantics, deterministic selection, and semantic Map geometry.
- No schema or product-behavior change.

## 8.18.19 — Final verifier headroom and native interaction contracts

- Re-shards the v7.7.3 generation/reconstruction property gate to 20 × 100 mutations / 10 reconstructions while preserving exactly 2,000 canonical mutations and 200 reconstructions; this removes SQLite/materialization teardown pressure from the hard 10-second item budget.
- Re-shards the v7.7.8 cross-layer soak to 32 × 10 mutations while preserving 320 mutations and the aggregate backup/publication/repair coverage, leaving deterministic TestClient teardown headroom.
- Makes the durable H1 workflow use Chromium's native double-click selection path instead of synthetic DOM ranges.
- Makes Span-resize tests target the terminal rendered Line route segment and verify its logical endpoint is beyond the current Span before dragging.
- Fixes the deterministic multi-line Notes fixture so escaped newlines reach browser JavaScript correctly.
- Schema remains 39.

## 8.18.18 — Final durable interaction test hardening

- Treats stroked SVG Map routes as attached geometry rather than relying on Playwright visibility semantics for zero-height `<line>` boxes; route drag targets are derived through `getScreenCTM()`.
- Centralizes durable manuscript block selection so historical H1 workflows explicitly position Breadmaker's real selection toolbar after creating a range.
- Makes the Notes flyer scroll fixture deterministically exceed the ten-line viewport with explicit multi-line content rather than relying on font wrapping.
- Removes the remaining fixed Maps-view delay from the modern Span-resize contract while preserving persisted-state synchronization.
- Schema remains 39.

## 8.18.17 — Observable-state browser synchronization

- Removes the last fixed-timing assumptions from the remaining durable H1, per-tab Scene, and Span-resize workflows.
- Adds stable automation hooks for selection Style, manuscript Navigator actions, View → Scenes, and Map Line routes.
- Span endpoint tests now drag toward the rendered route target and wait for persisted API geometry rather than an arbitrary pixel delta or sleep.
- Schema remains 39.

## 8.18.16 — Testability and interaction-contract hardening

- Adds stable `data-testid` hooks for fragile manuscript-selection, Marker, Canvas marquee, manuscript block, and Map Span-handle surfaces without changing product behavior.
- Browser tests now prefer application/API completion signals over fixed sleeps for the affected interactions. The Canvas marquee contract waits for active view/mode, visible marquee, actual autoscroll, and marquee teardown.
- Corrects the durable Marker workflow to reflect one-click highlighter semantics: active Marker selections auto-apply and suppress the ordinary selection toolbar; the toolbar/picker is tested after the highlighter is explicitly turned off.
- Splits the historical Maps direct-manipulation monolith into focused Line-junction, Station-drag, and Span-resize contracts. Durable E2E now contains 92 pytest items.
- Re-shards the v7.7.3 generation-reconstruction property gate from 8 × 250 mutations / 25 reconstructions to 10 × 200 / 20, preserving exactly 2,000 mutations and 200 reconstructions while leaving deterministic teardown headroom under the hard 10-second item budget.
- Adds `docs/TESTING.md` to codify stable locator, synchronization, isolation, and scope policy. Schema remains 39.

## 8.18.15 — Final durable interaction convergence

- Historical manuscript-selection workflows now drive the same `selectionchange` lifecycle as current browser contracts before using the selection toolbar.
- The direct Maps geometry workflow keeps Span-resize coverage on an independent unlocked Line instead of reusing geometry already mutated into a Junction.
- Verify Project protects the intentionally uppercase rendered section headings.
- Keeps per-item project isolation, complete durable accounting, and production feature behavior unchanged. Schema remains 39.

## 8.18.14 — Durable contract convergence

- Aligns historical durable E2E contracts with current selection-toolbar, Verify Project, Tab-menu, Scene-menu, Checkpoint comparison, and Maps persistence behavior.
- Keeps the 8.18.13 per-item project isolation and complete durable accounting architecture unchanged.
- No schema change and no production feature-behavior change.

## 8.18.13 — Durable lease isolation and release cache-buster repair

- Durable fresh-project setup now identifies itself as the same synthetic editor client used by the browser, so production lease protection cannot reject the harness between items.
- Durable page cleanup stops heartbeat work before releasing the editor lease, eliminating the release/reclaim race.
- Brand SVG cache busting is synchronized with the release VERSION again.

## 8.18.12 — Durable project isolation and timeout containment

- Durable E2E keeps one shared uvicorn/Chromium lifecycle for speed, but every pytest item now activates a fresh Breadmaker project before its workflow begins. Historical contracts no longer inherit markers, checkpoints, cuts, maps, chapters, tabs, or project-scoped preferences created by unrelated earlier tests.
- A call-phase 10-second timeout is now a durable worker containment boundary: the failure is written to JSONL, that browser worker exits, and the verifier resumes only the still-unrecorded items in a clean worker. The 10-second per-item execution budget is unchanged.
- When failures exist, the compact digest now always prints durable expected-vs-recorded accounting, including an explicit `complete` status when every durable item was observed.
- Browser-shell HTML/CSS/JS preparation is cached within the durable module to reduce repeated parsing overhead while pages themselves remain isolated.
- Historical contracts were aligned with current Project Explorer cards/Card terminology, current Cuts selection persistence, actionable toast text, current Map ruler spacing, and structural-queue completion for Span resizing.
- Oversized v4.12.10, v4.13.1, and v4.17 durable workflows are split at stable behavioral boundaries rather than receiving larger timeouts. The durable module contains 90 pytest items in this release.

## 8.18.11 — Durable tail recovery and historical UI contract cleanup

- Durable `test_e2e.py` keeps its fast shared-module execution path, but a poisoned hard-exit now resumes only the not-yet-executed tail in a fresh pytest/browser process against the same temporary E2E workspace. Setup-only records are retried; a timeout with a recorded call failure is not duplicated. One timeout can no longer erase later durable coverage.
- Updated historical Canvas/Maps contracts to current collection menus, collapsed Line color pills, View-popover zoom controls, connector endpoint behavior, and the three-theme Light → Dusk → Dark cycle.
- Split oversized History and Manuscript durable workflows so each real UI contract remains inside the hard 10-second item budget without weakening coverage. The durable module now contains 87 pytest items, all discovered regardless of historical `test_browser_…` versus `test_v…` naming.
- The hard test budget remains 10 seconds; the 6-second post-failure unwind window remains cleanup-only.

## 8.18.10 — Durable watchdog recovery and final contract cleanup

- Split the oversized first durable browser workflow into two independently bounded items without dropping coverage.
- Keep the 10-second per-item execution limit while giving an interrupted Playwright call enough bounded unwind time to emit its pytest report.
- Persist a synthetic JSONL timeout record before the hard-stop fallback so a killed durable process retains its actual timeout cause.
- Repair the 8.18.9 Canvas persistence source contract and advance the Review implementation-plan release marker.

## 8.18.9 — Durable E2E convergence and verifier diagnostics

- Fixed a real Canvas viewport race: delayed viewport/tool-mode persistence is now bound to the Canvas that queued it and is cancelled before switching, preventing Pointer mode from leaking into a newly opened Canvas.
- Durable Metro workflows now enable both required experimental gates (project feature + renderer opt-in) before opening Metro View.
- Historical durable Canvas/Maps checks now exercise the current interaction model: direct Card drawing, endpoint-only Connector retargeting, eight Card anchors, the SVG-mask brand mark, and the canonical Span-selection gesture.
- The unexecuted tail of the durable suite was statically audited too: Maps Line double-click/color editing now follows the current Line Details modal rather than retired Focus/inspector controls.
- Timed-out items now attempt a bounded Python-level teardown interrupt before the verifier hard-stops the aggregate process, improving the chance that later durable items still run and are accounted for.
- Compact failure reasons preserve the source assertion or Playwright wait predicate when pytest would otherwise reduce a failure to `assert False`, a truncated compound expression, or a generic `wait_for_function` timeout.
- Durable item accounting now counts every pytest function in `tests/test_e2e.py` (84 in this release, including three historical `test_v…` names), and the final digest prints an explicit ACCOUNTING section whenever expected and recorded counts differ.

## 8.18.8 — Regression convergence and durable E2E modernization

- Repaired Review duplicate/delete commands whose menu actions survived after their frontend implementations were lost.
- Writer suggestion acceptance now refreshes live manuscript state as well as the frozen Review Feedback view, so accepted prose is visible immediately after leaving Reviewer Mode.
- Preserved the hard 10-second per-test contract while giving timed-out tests a bounded cleanup grace so one browser teardown cannot terminate the remaining durable E2E module.
- Randomized structural reliability remains 200 total mutations, split across ten bounded 20-operation shards to preserve coverage with reliable headroom under the hard Core/API per-item budget.
- Durable Canvas/Maps workflows were updated from retired inspector/menu controls to the current direct-manipulation and compact-control contracts; experimental Metro workflows explicitly enable the project feature before testing it.
- Failure-reason selection now rejects pytest helper/noise lines and prefers concrete assertions, locators, expected/actual values, and browser exceptions.

## 8.18.7 — Verification stabilization and Review interaction repair

- Restored the missing Review Details pencil binding and hardened the current UI control audit so Review Details cannot silently become unreachable again.
- Updated historical Review browser workflows to the current Questionnaire library, realtime feedback autosave, reversible round lifecycle, Review Feedback suggestion decisions, and ellipsis-menu task/resolve commands.
- Preserved the hard 10-second per-test execution budget while disarming the watchdog during pytest failure formatting, so the original assertion/Playwright error is retained.
- Verification jobs now emit incremental per-phase JSONL result records in addition to JUnit, so durable aggregate E2E jobs retain child names, phases, counts, and failure reasons even if pytest exits abnormally.
- The verifier treats missing item accounting as a first-class failure instead of silently reporting an aggregate module as zero tests.
- Randomized reliability shards keep the same mutation/invariant workload while maintaining structural IDs locally instead of repeatedly downloading the complete bootstrap.

## 8.18.6 — Verification fidelity and Review regression repair

- Restored the complete Review Participants/Populate helper block that had been accidentally removed while callers and UI controls remained.
- Updated historical Review contracts to the current reversible-round, realtime-feedback, and auto-enable-on-questionnaire semantics instead of reverting intentional product behavior.
- Retained the older durability/recovery documentation contracts in concise archival sections so current release notes no longer erase architectural invariants.
- Verification now batches ordinary core tests by module for speed while keeping reliability/scale items isolated and retaining the hard per-test watchdog.
- Every pytest process emits JUnit results; aggregate durable E2E failures are expanded into individual failing child tests in the console digest/report.
- Failure reporting preserves setup/test-body/teardown phase information and useful assertion/exception signatures.

## 8.18.5 — Verification repair and reporting

- Corrected 8.18.4's biggest harness mistake: `tests/test_e2e.py` now runs once as the sequential module it was authored to be, preserving its module-scoped server/browser lifecycle while current UI-control audits remain isolated. This removes false regressions and cuts dozens of redundant Chromium/uvicorn startups.
- Hardened legacy chapter-specific E2E workflows so they explicitly enter a Chapter instead of depending on startup/history state.
- Updated stale durable-E2E expectations for intentional product changes: Project Center → project menu/Project Settings, Canvas Node → Card terminology, actionable Chapter headings, and the intentional Add Chapter Below menu item.
- The verifier no longer floods the console with full tracebacks by default. It ends with a grouped `FAILURE DIGEST` containing every failing test item and a short cause; `--verbose-failures` restores full console traces when needed.
- `verification-report.txt` still retains complete pytest output for deep diagnosis.
- Default core concurrency can use up to 8 workers on capable machines; browser concurrency remains conservative.

## 8.18.4 — Verification hardening

- `test` and `verify-release` now use one authoritative comprehensive verifier.
- Core/API modules run in isolated pytest processes with bounded parallelism; browser/E2E jobs use separate Chromium processes with conservative parallelism.
- The console shows a live progress bar/heartbeat with pass/fail counts, active jobs, elapsed time, the most recently completed failing item, and the oldest running job so verification never appears frozen or misattributes a concurrent failure.
- The release gate now includes the durable `tests/test_e2e.py` browser workflows in addition to foundational, 6.4-boundary, and all current 7.x/8.x contracts.
- Browser verification may not silently pass by skipping Chromium; a real browser runtime is a preflight requirement.
- New current E2E control audits exercise permanent navigation, Canvas/Maps/Reviews creation affordances, Review toolbar panels, Tasks creation, and Manuscript tool panels to catch dead-click/event-wiring regressions.
- Failed jobs are aggregated instead of stopping at the first failure, and `verification-report.txt` records the run for diagnosis.

## 8.18.3 — Review overview clarity and toolbar repair

- Compact Review scope summaries use chapter positions and counts, with full scope names available on hover.
- Draft Reviews show a clear no-feedback empty state with a Start Review… shortcut into Review Activity.
- Restored Participants, Markers, and Questionnaires toolbar panel bindings.
- Updated the permanent-navigation contract to the intended Reviews → Tasks order.

## 8.18.2 — Quiet side-panel creation affordance

Canvas, Maps, and Reviews now show the same small circular `+` after the final collection item. It creates the corresponding top-level item while preserving the existing `…` collection menus for advanced actions.

## 8.18.1 — Empty toolbar title consistency

Canvas, Maps, and Reviews now share the same muted toolbar title treatment when no renameable item exists, and the rename pencil is hidden until a real item is selected.

## 8.18.0 — Reversible Review rounds

Review rounds are now reversible editorial history rather than irreversible completion states. A round can be Open, Paused, or Closed. Closing preserves its frozen manuscript, feedback, returned Bread Review compatibility, and permanent linked Checkpoint(s). Closed rounds can be reopened without creating a new snapshot or Checkpoint. Starting a new round automatically closes the current live/paused baseline and creates the next baseline; reopening an older round pauses the currently live round so the older existing round can become live again. Returned `.breadreview` files continue to merge into the snapshot identity embedded in the file even when that round is Closed.

The main navigation now places Reviews before Tasks.

## 8.17.9 — Bread Review round-trip and Review feedback polish

- Review feedback defaults to Open; resolved inline feedback stays hidden until requested and renders with a green Resolved tag.
- Review Marker panel keeps “Review Markers” only as the panel title; controls inside use the simpler Marker wording.
- Bread Review export uses an optional passphrase field and asks for confirmation only when a passphrase is entered.
- Finish and Exit save dropped portable Bread Review files before closing Reviewer Mode.
- Dropping a Bread Review from the current project lets the user either open it as a reviewer for testing or import returned feedback into the writer project.
- Review modal fields use consistent label-to-control spacing.

---

# Retained durability and setup contracts

These older release contracts remain part of the current product even though the active release notes above focus on the 8.x UI and Review work.

## 7.4.1 Stage 1 completion — recovery, portability, and provider boundaries

Breadmaker keeps recovery data, portable project data, and destination/provider concerns separated. Backup destinations must never recurse into Breadmaker-managed project storage, and provider-specific behavior stays behind the backup-provider boundary.

### Google Drive uses user-owned OAuth credentials

Google Drive backup setup uses **your own Google Desktop OAuth Client ID**. Put the local desktop-client configuration in `google_oauth_client.json`; Breadmaker does not depend on a publisher-owned shared OAuth client.

## 7.7.4 History Restore + History UI

History restore never rewinds canonical authority in place. Restoring historical content produces the **next** canonical **generation**, preserving monotonic history and the ability to reason about what happened before and after the restore.

## Verification reliability contract

Every pytest item has a hard setup + call + teardown budget. **Core/API tests remain at 10 seconds; browser and durable E2E tests use 15 seconds** to absorb isolated server/Chromium startup and teardown variance. The budget resets for each test. The release verifier may run many healthy tests for much longer than either item limit in total; one individual hung test may not consume the rest of the verification run.


Auth UI theme parity applies only to the real Auth0 Universal Login page template. The Breadmaker-owned shell uses the Editor `light → dusk → dark` token sets and the same half-filled-circle theme control, while the Auth0-owned credential widget remains on the exact Breadmaker Light theme because Breadmaker does not rewrite Auth0 widget DOM. There is no local auth preview or preview runtime; adopting per-user runtime themes inside the Auth0 widget would require a separately reviewed ACUL boundary.


Checkpoint 11 second-pass hardening adds browser-bound initial login state, bounded/rate-limited public authorization starts, Auth0-user-bound signed TOTP enrollment intents, required Origin on real-auth mutations, and suppression of OAuth callback query strings from Uvicorn access logs.

Checkpoint 11 round-two hardening additionally pins the real-auth Host header to the configured public origin, rejects repeatedly percent-decoded redirect confusion, bounds account-link and step-up state stores, keeps callback failures generic, and explicitly forbids multi-worker hosted authentication until the in-memory session/OIDC/security stores are replaced by shared atomic state.
