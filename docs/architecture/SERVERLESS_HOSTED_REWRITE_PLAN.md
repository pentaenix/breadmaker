> **Historical architecture journal.** This document includes older milestone states and model/operator handoffs. For the current architecture, start with [Architecture](../../ARCHITECTURE.md) and [M12 source closure](../security/M12_SOURCE_CLOSURE.md). Do not use this file to deploy the hosted service.

> **ARCHITECTURE REFERENCE ONLY.** The current release status, agent instructions and revised M12/M13 milestone decisions live exclusively in the ZIP-root `SERVERLESS_HOSTED_REWRITE_TRACKER.md`. Historical deployment-phase prose below is intentionally preserved but superseded when it conflicts with that tracker.

# Architectural rewrite plan — historical design contract

**Canonical current status, releases, open risks and agent handoff:** see **`SERVERLESS_HOSTED_REWRITE_TRACKER.md`**. This plan retains architectural history; older release-specific status paragraphs below are superseded by that single tracker. Do not add more milestone-specific handoff Markdown files.

Current source candidate at the time of this revision: **Editor 10.5.15 / Cloud 10.4.11**, Editor awaiting owner verification. M12 is offline security hardening; M13 is manual GCP/GitHub/live acceptance. Hosted encryption is experimental, inactive and incomplete. Local Breadmaker is fully independent and needs no account, network, KMS, GCP or telemetry. **Agents must never run the test suites**; only the owner runs release verifiers.

---

## 2026-10-13 — M12E private Firestore Task/milestone encryption pilot (Editor 10.5.13 / Cloud 10.4.11)

Added hosted-only `HostedPrivateFirestoreFields` backed by existing AES-256-GCM/KMS envelope, explicitly injectable through hosted DomainStore and CloudProjectRepository for `task` and `task_milestone`. The user-defined record fields become ciphertext, operational IDs/timestamps/generation remain visible, tenant AAD binds project+entity+record. Normal hosted runtime still DOES NOT ENABLE encryption. Generic protected batch and project-package paths fail closed; authorized bounded list filtering occurs only after decryption. Eight targeted conformance cases authored and NOT run. No local SQLite/runtime/backups/GCP/Cloud files changed. `M12E_ENCRYPTED_FIRESTORE_HANDOFF.md` and coverage JSON document exact boundaries, unknown risks and next steps. Do not claim whole hosted data is encrypted. Editor 10.5.12 verification remained pending when M12E work began; human must run Editor 10.5.13 verifier (which also runs both strict dependency audits). Model must NEVER execute test suite.

# CURRENT STATUS — 2026-10-13 — M12D VERIFICATION CORRECTIVE (10.5.12)

Candidate **Editor 10.5.12 / Cloud 10.4.11** (Editor owner-verification PENDING). Last all-green Editor **10.5.9**, Cloud **10.4.11**. Editor 10.5.11 removed the 7-way `NameError` but owner verification found two cold-setup 10s timeouts and one `draft_2` milestone focus race. Editor 10.5.12 uses established module-scoped prewarm for `test_v10326.py`/`test_v700.py` while retaining per-item watchdogs, and guards delayed Tasks milestone auto-focus from stealing a second input. Full human Editor verifier required; Cloud 133/133 already green, byte-identical; agent must NEVER run suites. Local Breadmaker remains no-account/offline/no-GCP/KMS/telemetry; hosted encryption/metrics still inactive and incomplete. This is NOT a deployment release. Read `READ_THIS_FIRST.md` first.

---

# CURRENT STATUS — 2026-10-13 — M12D 10.5.11 CHAPTER MUTATION CORRECTIVE

Editor **10.5.11** / Cloud **10.4.11** candidate awaiting human verifier. Previous last fully green Editor **10.5.9** / Cloud **10.4.11**; owner confirmed Cloud still 133/133 green, Editor 10.5.10 ran 1593 passed / 7 failed / 1 skipped. M12D adds opt-in encrypted GCS body publication and authenticated reads through actual hosted block CRUD + atomic chapter replacement; real runtime remains inactive and Firestore metadata/other families/packages/recovery are NOT confidentially integrated. No automated activation or live GCP changes. Local `./run` remains completely accountless, offline, KMS/GCP-free. The agent MUST NEVER execute verifier/tests; owner supplies results. See `M12D_HOSTED_BLOCK_INTEGRATION.md` and `READ_THIS_FIRST.md`. M13 remains human-operated infrastructure/deployment; private alpha deferred.

---

## 2026-10-13 — M12D Core/API release corrective

The owner ran M12D Editor 10.5.10: 1,593 passed, 7 failed, 1 skipped. All seven failures shared `NameError: encoded is not defined`, affecting six inherited M8B chapter-replacement cases and one new encrypted replacement case. Cloud 10.4.11 is owner-confirmed fully green (133/133), and its files remain unchanged. The original `chapter_mutations.py` M12D edit accidentally omitted `structured, encoded = hybrid` in the `hybrid is not None` branch; 10.5.11 restores that tuple unpacking before immutable publication, preserving both the plain and opt-in encrypted paths. One focused AST guard `tests/test_v10511_chapter_hybrid_contract.py` is added; the existing runtime tests remain unchanged. No suite has been run by the agent; the human must run only Editor `./verify-release` and report the digest. Local runtime/accountless SQLite/backup files and Cloud remain unchanged; hosted encryption remains opt-in, incomplete, and disabled in normal runtime. No GCP deployment, KMS key, hosted telemetry or destructive deletion has been activated.

# CURRENT STATUS — 2026-10-13 — M12C ENCRYPTED OBJECT PILOT, NOT ACTIVATED

**Candidate Editor 10.5.9 / Cloud 10.4.11.** Last user-verified all-green Editor **10.5.8** / Cloud **10.4.11**. M12C adds an opt-in hosted immutable encrypted-object publication/readback path, **not** complete encrypted hosted persistence. Legacy Firestore/GCS data and recovery archives are not migrated or encrypted; leave all activation switches disabled. Local Breadmaker stays fully offline/accountless and Cloud 10.4.11 stays unchanged. Read `M12C_ENCRYPTED_OBJECT_HANDOFF.md`, `READ_THIS_FIRST.md`, security audit and coverage inventory before touching anything. User alone runs test suites; model does static checks only. No cloud resources deployed; M13 is operator-managed and private alpha deferred.

---

## 2026-10-13 — M12B Cloud-only release contract corrective (Cloud 10.4.11)

The owner reported **Editor 10.5.8 green**, including core + hosted `pip-audit --strict` preflights, and Cloud 10.4.10 with **132 green, one failed** stale version pin in `tests/test_repository_contract.py`. Identity policy and OpenTofu passed. 10.4.11 replaces brittle exact-patch contracts in `test_repository_contract.py` and `test_m12b_privacy.py` with a minimum supported version, three-part format, README first-line identity, and Identity Platform policy-version synchronization. Cloud README/AGENTS, `VERSION` and policy release were updated. Editor is unchanged. The agent did not run any suite. Hosted encryption/metrics, KMS provisioning, deployments and deletion remain DISABLED. Cloud verifier is user-owned and still required.

## 2026-10-13 — M12B release preflight integration (Editor 10.5.8 / Cloud 10.4.10)

Editor `./verify-release` now runs **two fail-closed, networked vulnerability scans** with the pinned `pip-audit`: the existing `requirements.txt` audit followed by a complete `requirements-hosted.txt` audit, which also resolves the core graph transitively. Both scans must pass before any test item starts. This is dependency resolution/advisory checking, not GCP deployment or installation of hosted SDKs. Core local runtime remains fully offline/accountless; verification has always needed network access for the original audit. Standalone `./audit-hosted-dependencies` remains optional. All cloud files stay byte-identical to 10.4.10. New source contracts in `breadmaker/tests/test_v1058_hosted_audit.py` require user verification; **agent MUST NOT run test suites**. Do not activate plaintext-hosted encryption or telemetry just because the verifier passes.

> **Latest scope decision (2026-10-12, M12):** User redefines **Milestone 12 as an offline security audit and hardening checkpoint**. GCP/GitHub/DNS/billing, real deployment and live acceptance are **Milestone 13**, explicitly operator-controlled. No private-alpha admission. This decision supersedes the historical M12/M13 text in sections 20–21 below. See `M12_SECURITY_AUDIT.md` for findings and release blockers. Editor 10.5.5 / Cloud 10.4.8 is the last operator-reported green baseline; 10.5.6 / 10.4.9 is awaiting verification.

> **Superseded rollout decision (earlier 2026-10-12):** Private alpha is postponed by the user.
> M10 and M11 should be source-complete and verifier-green before any live GCP
> apply. M12 becomes the first explicitly approved deployment and acceptance
> phase, not a commitment to admit alpha users. Production data-plane activation,
> public Hosting and delivery WIF are off until M12. M13 staging remains optional.

> Current checkpoint: **M12 security audit Editor 10.5.6 / Cloud 10.4.9 awaiting user verifier**. Source privacy hardening and dependency advisory gates are prepared. Live GCP apply/restore, effective IAM evidence, exhaustive coverage and production deletion remain gated.

# Breadmaker Serverless Hosted Rewrite Plan

**Planning baseline:** Breadmaker Editor `10.3.41` + Breadmaker Cloud `10.4.32`  
**Plan created:** 2026-10-05  
**Deployment state at plan creation:** nothing has been deployed; there is no hosted user data to migrate.  
**Canonical live status:** `SERVERLESS_HOSTED_REWRITE_TRACKER.md` in this archive/repository root.

> This document is the architectural and execution contract for replacing the current undeployed VM/Auth0 hosted design with a GCP serverless design while preserving local Breadmaker as-is.

---

## 0. Read this before touching code

### The model/operator split is mandatory

**The model must NOT execute the Breadmaker verifier or test suites.**

Unless the user explicitly changes this rule in a later conversation, the model/agent must not run:

- `./verify-release` in either repository;
- `./test`;
- `pytest`;
- Playwright/browser tests;
- Durable E2E;
- any wrapper that invokes the above;
- a live GCP deployment as a substitute for verification.

The model **may** run narrow non-test static checks such as:

- Python parsing/compilation checks;
- JSON parsing;
- JavaScript syntax checks;
- shell `bash -n` checks;
- searches/greps;
- archive integrity checks;
- generated-index refresh commands only when they do not execute the verifier/test harness;
- OpenTofu formatting only when the milestone explicitly requires it and it is not routed through the release verifier. Prefer leaving validation to the user unless needed to diagnose syntax.

### Verification workflow

At each verification gate:

1. The model updates `SERVERLESS_HOSTED_REWRITE_TRACKER.md` to `AWAITING_USER_VERIFICATION`.
2. The model gives the user the exact package and asks the user to run the verifier.
3. The **user** runs the verifier/test suite.
4. The user returns the compact failure digest/accounting.
5. The model diagnoses/fixes failures without running the suite itself.
6. A milestone is not `VERIFIED` until the user reports the required gate green.

A model must never write “verified,” “green,” or “passed” based only on static checks.

### Tracker discipline

`SERVERLESS_HOSTED_REWRITE_TRACKER.md` is not release notes. It is crash recovery state.

Update it:

- before beginning a milestone;
- after every atomic implementation block that changes ownership or architecture;
- before a large refactor;
- after a large refactor;
- before handing a ZIP to the user;
- immediately after receiving verifier results;
- whenever a blocker or decision changes the next action.

**Do not wait until the end of a milestone to update it.** If a chat dies, the tracker should identify the last known-good sub-step, changed files, unresolved questions, and the next safe action.

---

# 1. Why we are rewriting now

Breadmaker has no deployed hosted environment, no paying users, and no hosted data. This is the cheapest and safest point in the product lifetime to make a hard architectural correction.

The previous hosted architecture was designed around:

- a single persistent Compute Engine VM;
- a persistent filesystem/SQLite model;
- Auth0;
- process-local hosted session/OIDC/step-up state;
- staging before production.

That architecture is defensible, but it creates a non-zero fixed hosting bill and eventually requires a second migration to become horizontally scalable.

The new objective is different:

> If Breadmaker never grows beyond its creator, hosted Breadmaker should be able to sit mostly unused for approximately zero compute cost. If Breadmaker succeeds, the same architecture should scale without replacing its persistence/authentication foundation.

This plan therefore accepts a substantial hosted rewrite now.

---

# 2. Non-negotiable product boundaries

## 2.1 Local Breadmaker is frozen conceptually

The downloadable/local product remains:

```text
./run
  -> local runtime
  -> SQLite Schema 42 (until an independent local-product schema change is intentionally made)
  -> local filesystem assets/revisions/backups
  -> accountless core editing
  -> no cloud account required
  -> no secret required
  -> no network required for core editing
```

The hosted rewrite must **not** turn local Breadmaker into a thin client for GCP.

The rewrite may introduce interfaces/adapters around local code, but local behavior must remain first-class rather than a compatibility shim.

## 2.2 No legacy hosted compatibility is required

Nothing has been deployed. There is no production Firestore, Cloud Storage, Auth0 tenant data, or hosted SQLite data that must survive.

Therefore:

- old hosted persistence formats may be deleted rather than migrated;
- old Auth0-specific hosted code may be deleted after replacement is proven;
- old staging VM/OpenTofu modules may be removed once the serverless IaC supersedes them;
- no dual-write or migration bridge is required for nonexistent hosted data.

## 2.3 Infrastructure is code-owned

Recurring infrastructure configuration must not depend on dashboard clickops.

Target ownership:

- OpenTofu owns GCP resources/IAM/services/policies;
- source-controlled Firebase Hosting configuration owns rewrites/static hosting behavior;
- source-controlled workflows own build/deploy behavior;
- secret **containers/references/permissions** are declarative;
- secret **payloads** remain outside OpenTofu state and source control;
- one-time domain registrar nameserver delegation may be an operator bootstrap action if the registrar has no safe provider, but DNS records after delegation must be declarative.

## 2.4 Data safety beats convenience

The hosted rewrite must never trade manuscript durability for scale-to-zero.

A write is not “saved” until the authoritative cloud persistence operation has succeeded according to the data contract defined in Milestone 2.

## 2.5 Authorization remains Breadmaker-owned

Identity Platform authenticates users. Breadmaker remains authoritative for:

- project ownership;
- project membership;
- project roles;
- reviews/reviewer permissions;
- manuscript access;
- destructive operations;
- product entitlements.

Email is never a project capability and must not become an automatic account-linking authority.

---

# 3. Target architecture

```text
                           breadmaker.studio
                               marketing

                         app.breadmaker.studio
                                  |
                          Firebase Hosting
                    custom domain / TLS / edge
                                  |
                    source-controlled rewrite
                                  |
                                  v
                             Cloud Run
                       Breadmaker hosted API
                 request-based billing, min = 0
                                  |
          +-----------------------+-----------------------+
          |                       |                       |
          v                       v                       v
      Firestore              Cloud Storage         Identity Platform
  structured state       immutable manuscript      Google / Apple
  memberships/reviews    revisions + assets        email/password
  metadata/pointers      exports + backups         TOTP / reauth
          |                       |                       |
          +-----------------------+-----------------------+
                                  |
                       Cloud Run service account
                         least-privilege IAM
```

### Initial environment strategy

There is **no staging environment in the initial hosted rewrite**.

Development remains local. The first live cloud environment is:

```text
Production / Private Alpha
app.breadmaker.studio
```

It is not public launch merely because it is named production. Admission remains restricted during alpha. A staging environment is a deferred capability that can be added when there are paying users, external testers who justify it, or the user's finances change.

### Initial traffic/cost posture

Cloud Run:

- request-based billing;
- service-level minimum instances = `0`;
- bounded maximum instances initially (exact value chosen during IaC milestone; start conservatively);
- no background loop required for correctness;
- no correctness dependency on an individual instance surviving.

Current Google documentation confirms that Cloud Run scales to zero by default when there is no traffic and that request-based services with minimum instances set to zero are not billed for idle instances.

---

# 4. Target hosted persistence principles

## Firestore owns structured/queryable state

Examples:

- hosted user/profile record;
- project metadata;
- memberships and roles;
- document/chapter/scene metadata;
- review configuration and review state;
- annotations/comments metadata when suitably sized;
- current revision pointers;
- logical generation/CAS fields;
- account/product entitlement metadata;
- short-lived Breadmaker-specific workflow records if still needed after Identity Platform migration.

## Cloud Storage owns large/blob/immutable data

Examples:

- manuscript content revisions;
- checkpoint payloads;
- imported source artifacts;
- images/assets;
- exports;
- durable recovery packages;
- potentially immutable event/revision envelopes.

Never place a potentially large manuscript in a single Firestore document. Firestore documents have finite size limits and are not intended to be used as arbitrary blob storage.

## Revision publication pattern

The intended save pattern is:

```text
browser edit
   |
   v
Breadmaker API
   |
   +--> authorize project/user
   |
   +--> validate expected project/document generation
   |
   +--> write immutable GCS object using generation precondition
   |
   +--> Firestore transaction atomically advances current pointer/generation
   |
   `--> return committed revision/generation
```

Cloud Storage generation preconditions must be used where appropriate so retries/races fail explicitly rather than silently overwrite unexpected state.

Firestore transactions must be used for structured compare-and-swap updates that depend on values read in the same logical operation.

If a blob is written but the Firestore pointer transaction fails, the blob is an orphan—not corrupt authoritative state. Orphan cleanup is a separate idempotent maintenance concern.

## No in-process durable authority

Cloud Run may terminate instances. Therefore correctness must not depend on memory-resident:

- user sessions;
- OAuth state;
- account-link transactions;
- step-up grants;
- revision state;
- project locks;
- rate-limit state where global enforcement is required;
- backup scheduler state.

Local caches may exist only when loss of the cache cannot change security or durability semantics.

---

# 5. Target identity architecture

The rewrite replaces Auth0 for hosted Breadmaker with **Google Cloud Identity Platform** if Milestone 6 proves every security invariant can be satisfied.

The expected server-side browser pattern is:

```text
Identity Platform web sign-in
   |
   | browser SDK uses non-persistent/in-memory auth state
   v
short-lived ID token
   |
   | POST + CSRF to Breadmaker session exchange
   v
Cloud Run verifies fresh authentication
   |
   v
Identity Platform/Firebase Admin session cookie
HttpOnly + Secure + host-only
   |
   v
browser clears client-side auth state
   |
   v
Breadmaker APIs verify server session cookie
```

Firebase's official server-side session-cookie pattern explicitly supports non-persistent client auth state, ID-token-to-session-cookie exchange, `HttpOnly`/`Secure` cookies, recent-auth checks, revocation, and server-side verification.

### Required identity capabilities before Auth0 may be deleted

The feasibility gate must prove:

- Google sign-in;
- Apple sign-in architecture (implementation may wait on Apple Developer external prerequisite, but design cannot be impossible);
- email/password;
- verified email requirements;
- TOTP MFA;
- explicit account linking rather than implicit email linking;
- fresh reauthentication for sensitive operations;
- session revocation/sign-out-everywhere semantics;
- no persistent provider token storage in Breadmaker;
- no password/TOTP secret/recovery code storage in Breadmaker;
- Apple private-relay behavior is preserved;
- authorization remains Breadmaker-owned;
- private-alpha admission can be enforced without making email the permanent identity key.

Identity Platform currently documents Google, Apple, TOTP MFA and recent reauthentication. Its first 50,000 monthly active Tier-1 users (email/social/anonymous/phone category) are currently priced at $0, excluding message charges such as SMS. The plan deliberately avoids SMS MFA.

---

# 6. Repository strategy

The archive contains two repositories:

```text
breadmaker/        application/local + hosted implementation
breadmaker-cloud/  GCP infrastructure, policy, deployment controls
```

Do not merge them during this rewrite unless a milestone explicitly concludes that separation is harmful. Their release versions may advance independently.

### Current high-value application ownership map

Read these before broad searching:

- `breadmaker/AGENTS.md`
- `breadmaker/ARCHITECTURE.md`
- `breadmaker/src/api/CODEMAP.md`
- `breadmaker/src/api/route_manifest.json`
- `breadmaker/docs/CODE_INDEX.json`
- `breadmaker/src/storage/README.md`
- `breadmaker/src/runtime/contracts.py`
- `breadmaker/src/runtime/local.py`
- `breadmaker/src/runtime/hosted.py`
- `breadmaker/src/auth/README.md`

### Current high-value cloud ownership map

- `breadmaker-cloud/AGENTS.md`
- `breadmaker-cloud/ARCHITECTURE.md`
- `breadmaker-cloud/SECURITY.md`
- `breadmaker-cloud/TESTING.md`
- `breadmaker-cloud/IDENTITY_ARCHITECTURE.md` (historical Auth0 policy; it will be superseded, not silently mutated)
- `breadmaker-cloud/infra/`
- `breadmaker-cloud/policy/`
- `breadmaker-cloud/release-controls/`
- `breadmaker-cloud/scripts/`

---

# 7. Milestone map

| Milestone | Name | Primary result |
| --- | --- | --- |
| 0 | Freeze baseline + program contract | Rewrite plan/tracker exists; local baseline is protected |
| 1 | Hosted/local seam extraction | Cloud implementation can change without contaminating local SQLite/filesystem |
| 2 | Hosted data model specification | Every Schema-42 product concept has an explicit cloud ownership/mapping |
| 3 | Firestore structured-state adapter | Hosted structured state no longer depends on local workspace/SQLite files |
| 4 | Cloud Storage content/revision/asset adapter | Large/durable content uses immutable object publication + CAS pointers |
| 5 | Hosted application integration | All hosted product APIs operate through cloud adapters; local remains unchanged |
| 6 | Identity Platform security feasibility gate | Prove Identity Platform can satisfy Breadmaker security requirements before deleting Auth0 |
| 7 | Identity Platform implementation + Auth0 retirement | Hosted auth becomes stateless/server-cookie based and Auth0 runtime is removed |
| 8 | Cloud Run lifecycle/statelessness hardening | Hosted correctness survives instance death and concurrent instances |
| 9 | Backup/recovery/export/delete | Cloud data has tested independent recovery and safe deletion semantics |
| 10 | Serverless IaC rewrite | OpenTofu/Firebase config owns prod serverless infrastructure; old VM/staging IaC removed |
| 11 | Protected-main + production-alpha delivery | PR-only green main automatically deploys the exact immutable artifact to private production; rollback remains explicit |
| 12 | Offline security audit and hardening | Dependency/supply-chain review, tenant isolation, privacy/logs, storage, auth, CI/CD; findings fixed or gated |
| 13 | Operator-controlled deployment and live security acceptance | Real GCP/GitHub/DNS setup; negative IAM, backup/restore, security and rollback drills; no alpha users |

Milestones 0–12 are source-side program work; Milestone 13 is required for operator-owned live acceptance. Optional staging/auto-promotion is deferred beyond M13.

---

# 8. Milestone 0 — Freeze baseline and program contract

**Status at plan creation:** COMPLETE.

### Starts from

- Editor `10.3.41`.
- Cloud `10.4.32`.
- User reports the current baseline verifier green.
- Nothing deployed.
- Local Schema 42 is the working local persistence contract.

### Does

- Adds this plan and tracker only.
- Marks the previous VM/staging/Auth0 hosted architecture as superseded for future hosted work.
- Does **not** delete old code yet; deletion happens only after replacement milestones prove their behavior.
- Adds pointers in both repo `AGENTS.md` files so a future model finds this program immediately.

### Achieves

- One durable source of truth for the rewrite.
- A restartable multi-chat execution protocol.
- Explicit model/user test-execution separation.

### Verification

No new application/cloud behavior is introduced. Static file/archive checks are sufficient for packaging this planning snapshot. The pre-existing user-reported green baseline remains the implementation baseline.

### Exit criteria

- Plan exists.
- Tracker exists and says Milestone 0 complete / Milestone 1 next.
- Versions remain `10.3.41` / `10.4.32`; documentation-only packaging is not presented as a new product release.

---

# 9. Milestone 1 — Extract clean hosted/local seams

### Starts from

The current local implementation directly owns substantial persistence behavior under `src/storage`, `src/workspace`, `src/revision_store`, and hosted runtime composition in `src/runtime/hosted.py`.

`src/runtime/contracts.py` already provides useful seams such as `ProjectRepository`, `AssetStore`, identity/authorization and project context protocols. Reuse and improve these rather than inventing a second service-locator system.

### Does

1. Inventory every API/domain call that directly depends on SQLite paths, local workspace files, or local revision stores.
2. Extract the cross-cutting environment-neutral ports that can be defined correctly **before** the hosted data model is frozen: project catalog/membership, logical assets, project/account usage, identity/avatar content, and explicit native filesystem/history/backup bridges.
3. Do **not** invent fake Firestore-shaped CRUD methods for manuscript/review/canvas/map/task state merely to hide SQLite imports. Those feature-domain repository method sets are finalized immediately after Milestone 2 freezes the hosted data model.
4. Make the existing local implementations satisfy the extracted ports without changing local semantics.
5. Move safe cross-cutting API/backend edges to runtime composition and make every remaining SQLite/filesystem/revision dependency explicit migration debt. Freeze that debt in a machine-readable inventory; new direct local-engine dependencies are forbidden and the inventory may only shrink.
6. Do **not** add Firestore/GCS yet except perhaps empty adapter skeletons.
7. Add conformance/contracts around the local adapter, environment-neutral ports, and dependency inventory so interface extraction cannot silently change behavior.

### Likely owners/files

- `breadmaker/src/runtime/contracts.py`
- `breadmaker/src/runtime/local.py`
- `breadmaker/src/runtime/hosted.py`
- `breadmaker/src/storage/*`
- `breadmaker/src/workspace/*`
- `breadmaker/src/revision_store/*`
- focused API dependency/composition owners identified through `CODEMAP.md`

Do not create a giant `cloud.py`, `repository.py`, or `storage_service.py` dumping ground.

### Must remain untouched semantically

- local `./run` startup;
- local project locations;
- Schema 42 format;
- local backups/recovery behavior;
- current Editor UI behavior;
- accountless local product.

### Model-created verification coverage

Add tests proving:

- default runtime still builds local adapters with no GCP/Firebase dependency;
- local implementation passes the new repository contracts;
- local source code does not import Firestore/GCS/Identity Platform SDKs;
- project catalog/assets/usage hosted-facing contracts do not expose filesystem `Path` as a required domain concept where cloud code cannot satisfy it;
- all remaining direct local-engine API/backend dependencies match the frozen inventory exactly, so no new SQLite/filesystem coupling can appear unnoticed;
- explicit `runtime.native_*` bridges are optional and documented as Milestone-5 migration debt;
- old local feature tests continue to exercise the same semantics.

### User verification gate

User runs the complete Editor verifier and Cloud verifier.

Expected exit: all existing stages green plus new Milestone-1 contracts green.

### Achieves

The cloud rewrite can proceed behind explicit ports/adapters. Local Breadmaker is no longer at risk of becoming a conditional branch inside cloud-specific code.

### Tracker checkpoint

Record:

- protocols introduced/changed;
- APIs migrated to protocol ownership;
- any intentionally deferred local-path dependency;
- exact user verification result.

---

# 10. Milestone 2 — Specify the hosted data model completely

### Starts from

Milestone 1 repository interfaces are present, but hosted persistence is not implemented.

### Does

Create a canonical hosted data-model document inside `breadmaker/docs/` and machine-readable contract in the application or Cloud repo.

The milestone must map **every current local product entity that hosted Breadmaker needs**. It must not hand-wave “other tables.”

At minimum, inventory Schema 42 and associated filesystem/revision state for:

- projects and immutable project IDs;
- profiles/users;
- project members/roles;
- document tabs;
- chapters/scenes/manuscript blocks;
- markup labels/icon markers;
- annotations;
- comments/threads/messages;
- bookmarks;
- cuts;
- checkpoints;
- canvases/nodes/edges/versions;
- maps/tasks and any other API-visible domains present in the current schema;
- reviews and review entries;
- assets;
- revision/history records;
- trash/recovery metadata;
- exports/import packages.

For each entity, state:

- Firestore collection/document path or GCS object path;
- canonical ID type;
- parent/ownership relationship;
- authorization lookup needed before access;
- expected maximum size;
- mutation concurrency rule;
- delete/tombstone rule;
- backup/recovery representation;
- whether the local/hosted representation intentionally differs.

### Required architecture outputs

Define and freeze:

1. **Hosted Project ID** — keep current immutable public project ID semantics; ID is not a capability.
2. **Hosted user identity** — Identity Platform UID versus separate Breadmaker user ID decision. Do not use email as the durable key.
3. **Generation/CAS model** — all mutable aggregate roots that can be edited concurrently must expose a logical generation/version.
4. **GCS object naming** — deterministic tenant/user/project namespace with no user-controlled path traversal.
5. **Immutable revision envelope** — format version, content hash, logical generation, author UID, timestamps, object generation.
6. **Tombstones** — destructive actions first make authoritative metadata inaccessible; physical garbage collection can be delayed/idempotent.
7. **Orphan policy** — failed multi-service writes can produce unreferenced immutable blobs but never referenced half-writes.
8. **Index plan** — required Firestore indexes are source-controlled.

### No live cloud dependency

This milestone is specification + pure contract code only. Do not create GCP resources yet.

### Model-created verification coverage

- machine-readable schema parses;
- every hosted-required Schema-42 entity is mapped exactly once;
- no Firestore document is designated as an unbounded manuscript/blob container;
- every GCS mutation class states its precondition/idempotency behavior;
- authorization-neutral project ID invariant is preserved;
- no email-as-identity mapping exists.

### User verification gate

Full Editor + Cloud verifier.

### Achieves

There is a reviewable cloud schema before code starts scattering Firestore collection names through the application.

---

# 11. Milestone 3 — Implement Firestore structured-state adapter

### Starts from

Milestone 2 has a frozen hosted schema/data contract and Milestone 1 has repository interfaces.

### Does

1. Add a dedicated hosted Firestore persistence package; keep Google SDK imports inside the hosted adapter boundary.
2. Implement structured project/user/membership/domain repositories according to the frozen mapping.
3. Use Firestore transactions for CAS/read-modify-write operations.
4. Make project authorization queries explicit and fail closed.
5. Ensure cross-user/nonexistent project responses preserve enumeration resistance where current hosted contracts require it.
6. Make retry behavior idempotent; never translate a retry into a duplicate logical entity accidentally.
7. Ensure no request requires sticky instance memory.

### Testing strategy without credentials

Normal verifier tests must use deterministic fake/in-memory Firestore client contracts or narrowly mocked Google client interfaces. They must never require ADC or a live project.

An optional emulator/live integration command may be added, but it is **not** part of the model-executed workflow and must not replace deterministic release tests.

### Model-created verification coverage

- create/read/update/delete and generation conflicts;
- two writers racing on the same generation;
- cross-user project access denial;
- role/membership changes;
- idempotent create/retry semantics;
- transaction retry safety;
- missing document vs forbidden document external response equivalence where required;
- no local SQLite import in hosted Firestore package;
- no GCP dependency in local runtime.

### User verification gate

Full Editor + Cloud verifier.

### Achieves

Hosted structured state is cloud-native and horizontally safe; local SQLite remains unchanged.

---

# 12. Milestone 4 — Implement Cloud Storage content/revision/asset adapter

### Starts from

Structured state can be persisted in Firestore, but large content/assets still need a durable object model.

### Does

1. Implement hosted object storage behind the Milestone-1 interfaces.
2. Use immutable object keys for committed revisions/checkpoints.
3. Use Cloud Storage generation preconditions for create/replace operations.
4. Store content hashes and object generation/identity in authoritative Firestore metadata.
5. Implement safe streaming upload/download limits using existing hosted image/content security boundaries.
6. Keep image decoder restrictions from current security policy.
7. Implement deterministic orphan discovery/cleanup contract without making cleanup part of the write commit.
8. Ensure exports can be produced without writing irreplaceable state to Cloud Run's disposable filesystem.

### Critical save invariant

Never update the Firestore “current revision” pointer until the immutable object exists and its expected hash/generation has been obtained.

Never report save success if pointer publication fails.

### Model-created verification coverage

Use a fake object store that supports generation-match semantics. Test:

- `ifGenerationMatch=0` create-only behavior;
- duplicate retry;
- stale expected-generation failure;
- blob success + pointer failure => orphan only;
- pointer success never references missing blob;
- content hash mismatch rejected;
- malicious/path-traversal object names impossible;
- asset size/type limits preserved;
- local asset/revision code unchanged semantically.

### User verification gate

Full Editor + Cloud verifier.

### Achieves

Hosted manuscript/content durability no longer depends on a persistent VM filesystem.

---

# 13. Milestone 5 — Integrate all hosted product APIs with cloud adapters

### Starts from

Firestore and GCS adapters exist independently.

### Does

1. Build a new hosted runtime composition using cloud adapters.
2. Migrate every hosted route/domain to the adapter boundary.
3. Remove hosted assumptions about `BREADMAKER_HOME`, project directories, SQLite locks, and durable local filesystem paths.
4. Keep local composition on the existing local implementations.
5. Implement cloud-native project create/open/switch/import/export semantics.
6. Make import from `.breadmaker` explicitly create a new hosted project identity where current local semantics require new identity on import.
7. Ensure direct `/p/<project-id>` navigation still authorizes membership before serving project data.
8. Keep all project IDs authorization-neutral.

### Special attention areas

- Undo/redo is working-session state and may remain browser/process-local only if losing an instance cannot corrupt committed state. Document the exact boundary.
- Local revision history and hosted durable revision history may have different physical implementations but must expose coherent product semantics.
- Any background scheduler currently living in-process must be removed from hosted correctness paths.

### Model-created verification coverage

Add a hosted-runtime conformance suite that uses fake Firestore/GCS adapters and exercises the same major product flows as local:

- create/open/rename/delete/restore project;
- manuscript save/reload;
- chapters/scenes/tabs;
- annotations/bookmarks/comments;
- reviews;
- canvases/maps/tasks;
- assets;
- checkpoint/revision;
- import/export;
- unauthorized project access.

### User verification gate

Full Editor + Cloud verifier including browser/E2E.

### Achieves

Hosted Breadmaker can function without SQLite or a persistent server filesystem. No live GCP yet.

---

# 14. Milestone 6 — Identity Platform security feasibility gate

**This is a stop/go security milestone. Do not delete Auth0 first.**

### Starts from

Hosted data is cloud-native, while current identity implementation is still Auth0-oriented.

### Does

Research and prototype only the minimum needed to prove the following with current Google documentation/SDK behavior:

1. server-side session cookie creation/verification works with Python Admin SDK;
2. client auth persistence can be disabled/non-persistent before session exchange;
3. recent `auth_time` can be required before creating a Breadmaker server session;
4. Google sign-in maps to a stable Identity Platform UID;
5. Apple sign-in architecture supports private relay and first-login metadata behavior;
6. email/password can require verified email;
7. TOTP MFA can be enabled and enforced according to Breadmaker's policy;
8. fresh reauthentication is available for sensitive operations;
9. explicit account linking can be implemented without automatic email linking;
10. sign-out-everywhere/session revocation can be implemented;
11. user signup can be restricted for private alpha without relying on public project IDs or email as durable authorization;
12. provider credentials/secrets can be provisioned without committing them to source or OpenTofu state.

### Important current provider facts to verify, not blindly assume

Current Google provider documentation exposes Identity Platform configuration for:

- disabling end-user signup/deletion;
- MFA state;
- TOTP provider configuration;
- authorized domains.

However, provider/client secrets may be represented in raw state for some resources. The implementation must **not** accept that merely because OpenTofu can mark a value sensitive.

### Feasibility decision record

At milestone end, write `breadmaker-cloud/IDENTITY_PLATFORM_ARCHITECTURE.md` and a machine-readable policy containing:

- accepted session model;
- MFA policy;
- linking policy;
- reauth policy;
- revocation model;
- private-alpha admission model;
- secret-provisioning model;
- unresolved external prerequisite such as Apple Developer membership.

### Stop condition

If Identity Platform cannot meet a required security invariant without a material downgrade, stop. Do not weaken the invariant merely to eliminate Auth0 cost. Reevaluate provider/architecture with the user.

### User verification gate

Full verifier for any code/contracts added. Any optional provider/emulator experiment is operator-run and separately documented.

### Achieves

A reviewed go/no-go decision. Only a `GO` permits Milestone 7.

---

# 15. Milestone 7 — Implement Identity Platform and retire Auth0 hosted runtime

### Starts from

Milestone 6 is `GO` and its architecture policy is frozen.

### Does

1. Implement Identity Platform web login UI/integration using the approved non-persistent client-session pattern.
2. Exchange fresh ID tokens for `HttpOnly`, `Secure`, host-only server session cookies.
3. Verify session cookies server-side; define revocation checking cadence/requirements.
4. Implement Google, email/password + TOTP, and explicit linking/re-auth flows.
5. Implement Apple when external Apple prerequisites are available; otherwise keep a hard launch blocker recorded rather than fake support.
6. Replace Auth0-specific account/security routes with provider-neutral Identity Platform operations.
7. Preserve Breadmaker authorization/project membership checks.
8. Remove Auth0 runtime secrets, Management API operators, deployment desired state and provider-specific runtime code once no code path references them.
9. Keep historical docs in an archive/change history if useful, but current architecture docs must not claim Auth0 is active.

### Security invariants

- Browser JavaScript never receives project authorization authority.
- Provider token persistence is disabled after server-session exchange.
- Passwords/TOTP seeds/recovery codes never enter Breadmaker persistence.
- Email is not durable account identity or auto-link authority.
- Sensitive account operations require fresh reauthentication.
- Session cookie is not readable by JS.
- CSRF protection applies to session exchange/logout/state-changing requests.
- Sign-out-everywhere actually revokes Identity Platform sessions/tokens before/with local cookie invalidation according to policy.

### Model-created verification coverage

- token/session exchange happy path and stale `auth_time` rejection;
- CSRF rejection;
- invalid/expired/revoked session cookie;
- Google identity;
- email verification requirements;
- mandatory TOTP policy tests;
- sticky enrolled MFA policy if retained;
- explicit link requires proof of both identities;
- cross-user linking conflict;
- fresh reauth for security operations;
- logout and global revocation;
- local runtime cannot instantiate Identity Platform.

### User verification gate

Full Editor + Cloud verifier.

### Achieves

Hosted authentication is cloud-native, lower fixed cost, and no longer depends on process-local OAuth/session stores.

---

## Milestone 8 checkpoint implementation status (2026-10-09)

M8A (`10.3.62`/`.63`) covers instance-independent canonical state, request-scoped idempotency for supported creates/imports, bounded uploads and disposable containers. M8B (`10.3.64`) adds the reviewed whole-chapter Firestore atomic mutation/receipt, consistency-fenced composite reads, fail-closed unsupported hosted bulk routes, and SIGTERM/timeouts/concurrency policy. Combined Editor+Cloud user verification is pending. Real Cloud Run infrastructure settings are owned by M10, not this source milestone.

# 16. Milestone 8 — Cloud Run lifecycle and horizontal-safety hardening

### Starts from

Hosted persistence and identity are externalized.

### Does

Audit the entire hosted runtime for assumptions invalid under Cloud Run:

- instance can die after any request;
- filesystem is disposable;
- more than one instance may serve requests;
- requests after scale-from-zero may hit a fresh process;
- background threads/timers are not reliable durable schedulers;
- SIGTERM may happen during instance shutdown;
- deployment creates new revisions.

Refactor remaining process-local authority out of correctness/security paths.

### Required design outcomes

- no required sticky session affinity;
- no project write lock that exists only in one process;
- idempotency keys for operations that can be retried by browser/proxy/client;
- bounded request sizes/timeouts;
- startup does not require writable persistent local paths;
- temporary files use ephemeral storage only for reconstructible/transient work;
- long-running work over Hosting's request timeout is split into jobs or direct Cloud Run endpoint design where appropriate;
- Cloud Run max instance/concurrency settings are intentional cost/safety controls, not correctness requirements.

### Kill/restart test model

Add deterministic tests that construct a new hosted application/runtime between logical requests and prove:

- committed project data survives;
- authenticated session remains valid according to policy;
- one-shot operations cannot be replayed merely because process memory reset;
- no durable state is missing.

### User verification gate

Full verifier.

### Achieves

Hosted Breadmaker is truly serverless-safe, not merely “running our VM app in Cloud Run.”

---

# 17. Milestone 9 — Cloud backup, recovery, export, and deletion

### Starts from

All authoritative hosted state lives in Firestore/GCS.

### Does

Define a recovery strategy proportional to private alpha but safe enough for the only copy of a manuscript.

At minimum:

1. immutable manuscript revisions already provide historical content recovery;
2. GCS bucket retention/version/lifecycle policy is source-controlled;
3. Firestore metadata recovery has an independent strategy;
4. user-triggered `.breadmaker` export produces a portable recovery artifact;
5. hosted export can be restored into local Breadmaker where format semantics permit;
6. destructive project deletion uses tombstone/retention semantics before physical purge;
7. account deletion enumerates owned data and follows a documented irreversible workflow;
8. scheduled maintenance/backup uses Cloud Scheduler/Run Job or another explicit serverless mechanism—not a hidden in-process timer;
9. backup and restore are tested as operations, not merely documented.

### Recovery bundle implementation note (Editor 10.3.78, user verification pending)

The explicit job-invoked schedule policy, immutable hosted-v3 daily archive receipt,
independent offline recovery drill and local-format compatibility audit are now
implemented in source. A real Cloud Scheduler/Run Job identity, verified GCS
retention/IAM, independent Firestore/Identity metadata recovery and live disaster
drill are still M10/M12 deployment gates. Rich frozen Canvas/Review history is
not representable in local Schema 42 and continues to block local-v2 conversion;
never claim full local history parity or silently drop that content. The hosted-v3
recovery archive is still the comprehensive hosted content format.

### Cost rule

Do not enable an expensive backup product by default merely because it exists. Choose a private-alpha recovery floor, document what it protects, and make any paid retention/PITR feature explicit.

### Model-created verification coverage

- export then restore content-equivalence;
- restore into empty hosted project/account;
- deletion cannot accidentally purge another user's objects;
- failed purge is resumable/idempotent;
- recovery metadata points only to existing/verified objects;
- retention rules cannot be disabled by normal app runtime identity.

### User verification gate

Full verifier.

### Achieves

Scale-to-zero does not compromise manuscript recovery and the cloud copy is never the only irrecoverable representation.

---

# 18. Milestone 10 — Rewrite Breadmaker Cloud IaC for serverless production

### Starts from

Application runtime is cloud-native and verifier-green. No cloud resources have been deployed yet.

### Does

Hard-rewrite the undeployed cloud infrastructure. Remove VM/staging resources that no longer describe the target system.

### Target production resources

At minimum, declaratively manage:

- one production GCP project (unless bootstrap/control-plane project separation remains justified);
- required APIs;
- Artifact Registry repository;
- Cloud Run service;
- dedicated Cloud Run runtime service account;
- Firestore Native database;
- GCS content/assets/recovery bucket(s);
- IAM bindings scoped to exact resources;
- Identity Platform project configuration where safe to manage in provider state;
- Firebase project/Hosting site;
- Firebase Hosting custom domain resource if provider support is acceptable;
- `app.breadmaker.studio` DNS through a declarative DNS provider;
- Secret Manager containers if still needed;
- logging/monitoring/budget alerts;
- deployment WIF identities;
- state bucket/bootstrap protections.

### Cloud Run baseline

Initial production/private-alpha policy:

- request-based billing;
- minimum instances `0`;
- small explicit CPU/memory allocation chosen from measurement;
- conservative max instances to cap surprise cost;
- concurrency set intentionally after profiling;
- deletion protection on production service where provider supports it;
- immutable image digest, never `latest`;
- runtime service identity only has required Firestore/GCS/other API permissions.

### Firebase Hosting

Prefer source-controlled `firebase.json` rewrites to Cloud Run. Hosting custom domain resource can be managed through the Google beta provider if current provider support is accepted. No dashboard-only routing configuration is allowed.

### Identity Platform IaC warning

Some Identity Platform provider resources expose sensitive fields in raw OpenTofu state. Secret provider keys/client secrets must not be put into state to satisfy “IaC purity.”

The correct split is:

- IaC: provider enabled/config skeleton, permissions, allowed domains, MFA policy when safe;
- privileged operational secret provisioning: secret payload only;
- code-owned reconciliation script: verifies provider configuration without printing secret material.

This is still no-clickops: the operation is scripted/reviewable; the secret value simply does not enter state.

### DNS

If `breadmaker.studio` is not already managed by an OpenTofu-supported DNS provider, choose one during this milestone. Prefer Cloud DNS for all-GCP ownership unless another existing provider has a compelling reason. A one-time registrar nameserver change may be unavoidable, but individual records thereafter are IaC-owned.

### Remove obsolete architecture

Delete or archive current undeployed resources for:

- staging Compute Engine service;
- persistent VM manuscript disk;
- Google load balancer/NAT design;
- Auth0 deployment artifacts;
- assumptions that production is disabled because staging must exist first.

Do not leave dormant resources in the active plan “just in case.” Git history preserves them.

### Cloud verifier requirements

The normal verifier remains credential-free and non-deploying. It must validate:

- `tofu fmt`/`validate`;
- policy contracts;
- no secret payload in source/state inputs;
- no accidental min instance > 0;
- bounded max instances;
- immutable image requirement;
- runtime IAM least privilege;
- protected data buckets/database cannot be destroyed by normal release workflow;
- Firebase/Identity config source ownership;
- no old VM/staging compute remains active in current desired state.

### User verification gate

Full Editor + Cloud verifier.

### Achieves

The repository describes the actual low-fixed-cost production architecture before a single production resource is created.

---


### M10E corrective and environment-strategy decision gate (2026-10-12)

The Editor 10.5.4 user verifier passed 1,564 passed, 0 failed, 1 skipped. Cloud 10.4.4 had only one text-only security contract red: a forbidden term appeared in a nonexecutable docstring of the offline activation gate. Cloud-only 10.4.5 corrects this without changing any activation authority. The complete verifier remains the user's gate.

**Important plan deviation:** M10A–E build a staging-first infrastructure path, while the canonical strategy in Sections 3, 19–21 expressly requires production/private alpha first and defers staging to M13. Neither strategy has been deployed. Before any live apply, explicitly decide whether to (a) adapt the serverless infrastructure to private-alpha production-first, retaining optional staging code inactive, or (b) amend the cost/rollout strategy to staging-first with full user approval. Source verification alone does not resolve this.

# 19. Milestone 11 — Protected main + automatic private-alpha delivery

### Starts from

Serverless production IaC exists, is verifier-green, but no normal development push can mutate the live environment. There is still no staging environment.

### Does

1. Make `main` a protected production branch. Every change to `main` must arrive through a pull request; direct pushes are forbidden.
2. Make the complete Editor + Cloud verification jobs required status checks for merge. A PR whose current head is red, cancelled, stale, or missing a required check cannot merge.
3. Keep the solo-developer workflow usable: a PR is mandatory, but an independent reviewer approval is **not** required while the user is the only maintainer unless the user later opts in.
4. Manage the GitHub branch/ruleset policy declaratively (GitHub provider/API-backed IaC owned by the repository), not as recurring clickops. Secret/token payloads remain external to state/source.
5. After a protected PR is merged to `main`, automatically:
   - rebuild/verify the release metadata expected for that exact commit;
   - build the hosted container once;
   - scan it under the repository's fail-closed image policy;
   - push an immutable Artifact Registry image;
   - resolve and record the exact `@sha256:` digest;
   - plan production-alpha IaC/deployment changes;
   - run machine-readable safety checks;
   - deploy **that exact digest** to `app.breadmaker.studio`;
   - run non-destructive post-deploy health/smoke checks.
6. A pull-request workflow may verify/build, but it must never possess production deploy authority. Production deploy authority exists only for the post-merge `main` workflow identity.
7. Keep rollback explicit and boring: an operator workflow can redeploy the previously recorded known-good digest without rebuilding it.
8. Keep private-alpha admission enabled. Automatic deploy to the production environment does **not** mean public launch.
9. Do not create a staging environment merely to support this pipeline.

### Required protected-main invariants

The repository must encode and test that:

```text
change
  -> pull request required
  -> required Editor verifier GREEN
  -> required Cloud verifier GREEN
  -> merge to protected main
  -> automatic production-alpha deploy
```

And never:

```text
feature branch / arbitrary push / pull_request event
  -> production credentials
  -> production apply
```

Required-check names must be stable and owned by the workflow contracts so branch protection cannot silently point at obsolete jobs.

### IaC / GitHub ownership

The final repository should declaratively own:

- the branch/ruleset requiring PRs for `main`;
- required status-check names;
- direct-push prohibition;
- the production-alpha GitHub Environment if one is retained as the OIDC trust boundary;
- Workload Identity Federation trust restricted to the exact repository/workflow/ref/environment needed for post-merge deployment;
- least-privilege production deploy service account roles.

The user may perform one-time bootstrap needed to let IaC manage repository rules, but the steady-state rule itself must not live only in the GitHub UI.

### Model-created verification coverage

Add contracts proving:

- `main` requires a PR;
- Editor and Cloud verification checks are required before merge;
- no independent approval is required by default for the one-maintainer phase;
- PR workflows cannot deploy production;
- arbitrary branch pushes cannot deploy production;
- only a successful post-merge `main` workflow can assume the deploy identity;
- deployment uses an immutable digest, never `latest`;
- the deploy job consumes the digest produced for the merged commit;
- rollback accepts only a previously recorded immutable digest;
- private-alpha admission remains enabled;
- there is still no staging resource requirement.

### User verification gate

The user runs the full Editor + Cloud verifier. The model does **not** execute the verifier or test suite.

Expected exit: all existing stages green plus branch-protection/workflow/IaC contracts green. A repository settings/API inspection should also show the enforced PR + required-check rule before the live deployment milestone.

### Achieves

During the private-alpha phase, `main` behaves like the production branch: only reviewed-through-PR, verifier-green commits can land, and every landed commit is automatically delivered to the restricted production-alpha environment. Staging remains unnecessary until real users make blast-radius isolation worth its cost.

# 20. Historical Milestone 12 — Live private-alpha deployment (SUPERSEDED by M12 security audit)

### Starts from

Everything through Milestone 11 is user-verifier-green. This is the first point where live GCP resources should be created.

### Operator-only prerequisites

- GCP billing account;
- project/bootstrap values;
- domain/DNS authority;
- Google provider OAuth configuration as required;
- Apple Developer credentials only if Apple is being activated now;
- no secret values committed to repository.

### Deploy

Use the source-controlled bootstrap/apply workflow. Avoid console resource creation except an unavoidable external registrar/bootstrap action explicitly documented by IaC output.

### Live smoke checklist

At minimum verify:

#### Scale-to-zero / lifecycle

- Cloud Run min instances is 0;
- allow service to reach zero;
- first request cold-starts successfully;
- data written before scale-to-zero survives cold start;
- redeploying a new revision preserves all project data;
- rolling back image digest preserves all project data.

#### Identity

- Google login;
- email/password + required TOTP if enabled for alpha;
- Apple if external prerequisite is available;
- session survives Cloud Run instance replacement according to policy;
- logout;
- sign-out-everywhere/revocation;
- fresh reauth for sensitive action;
- unauthorized/non-admitted account cannot access alpha projects;
- account linking only through explicit proof.

#### Project authorization

Use at least two identities if possible:

- user A cannot read user B project by knowing ID;
- nonexistent/cross-user behavior does not leak useful enumeration differences;
- member roles behave correctly.

#### Data

- create manuscript;
- type/save/reload;
- chapters/scenes;
- annotations/bookmarks/comments;
- reviews;
- assets;
- checkpoint/revision;
- export;
- restore/import into a separate project or local copy;
- project deletion/recovery according to policy.

#### Security/logging

Inspect logs while intentionally causing failures. Verify logs do not contain:

- passwords;
- TOTP secrets/codes;
- provider tokens;
- session-cookie values;
- raw secret payloads;
- Apple private keys;
- unnecessary manuscript content.

#### Cost controls

- billing budget + alerts configured;
- Cloud Run max instances bounded;
- no min instances > 0;
- no accidental load balancer/VM/NAT fixed-cost remnants;
- Firestore/GCS free/low-cost usage visible;
- current monthly forecast recorded in tracker/runbook.

### Completion definition

Checkpoint/serverless rewrite is not complete because “the page loaded.” It is complete when the above live smoke passes and the user has a recoverable external copy of a test manuscript.

### Achieves

`app.breadmaker.studio` is a real private production alpha that can scale to zero and survive instance death/redeployment.

---

# 21. Historical Milestone 13 — Deferred staging and automatic promotion (SUPERSEDED by M13 operator deployment)

**Not part of the current cost-conscious rewrite.**

Trigger only when:

- there are paying users;
- there are enough external testers to justify an isolated environment;
- a job/financial situation makes the extra environment trivial;
- release frequency/risk makes direct manual production promotion too costly.

Then add:

```text
main green
   -> automatic staging deployment
   -> live smoke / soak
   -> manual production promotion of exact tested digest
```

Eventually production promotion may become automatic after a defined soak/health policy. Never rebuild between staging and production.

---

## Current M12 / M13 execution contract (supersedes sections 20–21)

**M12:** audit code, dependencies, tenancy, authentication, logs and storage privacy, recovery/delete, and CI/CD; repair source issues; run user-owned full verification and hosted dependency advisory scan; record findings and explicitly unresolved live-risk gates. Report: `M12_SECURITY_AUDIT.md`. Nothing provisioned or exposed.

**M13:** user/operator GCP billing/project, real IAM, GitHub rulesets/WIF, image digest/scan, controlled Cloud Run deploy, Identity Platform, domain/Hosting, logging privacy, two-user isolation probes, immutable backups/isolated restore, cross-provider deletion proof, cost controls and rollback. No private alpha until separately approved. Optional staging/auto-promotion is a later program decision.

---

# 22. Versioning strategy during the rewrite

Do not create a single enormous release that changes everything at once.

Each milestone should normally advance:

- Editor patch version when application code/contracts change;
- Cloud patch version when cloud policy/IaC/workflow changes;
- both only when both actually change.

Three-part versions remain mandatory.

The planning snapshot itself does **not** bump either version because it changes no product/cloud behavior.

A milestone may require multiple corrective patch releases until the user verifier is green. Stay in that milestone until green; do not carry red verification into the next milestone.

---

# 23. Testing strategy for a dual-backend product

The rewrite should result in three test layers.

## Layer A — domain/repository conformance

Run the same semantic contract against:

- local adapter;
- fake hosted adapter / Firestore+GCS simulation.

Purpose: local and hosted may store differently but must honor shared product behavior.

## Layer B — runtime-specific tests

Local tests prove:

- no account/network requirement;
- SQLite/filesystem/recovery behavior.

Hosted tests prove:

- authorization;
- CAS/concurrency;
- idempotency;
- stateless instance behavior;
- session-cookie behavior;
- cloud object publication semantics.

## Layer C — live operator smoke

Not part of ordinary offline verifier. Used only after infrastructure exists to prove actual provider wiring.

The model does not run Layer C unless the user explicitly asks and supplies/authorizes the environment operation.

---

# 24. Security invariants carried forward from the Auth0 work

The implementation provider changes; these principles do not:

- local core editing is authentication-free;
- project IDs are identifiers, never capabilities;
- authentication and authorization are separate;
- browser claims do not grant project authority;
- do not auto-link accounts by matching email;
- sensitive operations require fresh proof;
- recovery may not silently downgrade MFA;
- secrets/tokens/passwords/TOTP materials are not logged;
- credentials are not committed;
- runtime uses least-privilege service identity;
- state-changing requests require same-origin/CSRF defense appropriate to the new session model;
- redirects are allowlisted/canonicalized;
- account/user enumeration remains minimized;
- provider errors exposed to users remain generic where detailed taxonomy creates an oracle;
- destructive operations are ordered so partial failure fails safe;
- authorization checks precede consuming one-shot grants where applicable;
- user-controlled filenames/paths never become raw cloud object paths;
- uploaded content remains hostile input;
- current image security ceilings/decoder isolation remain in force unless separately reviewed.

Do not discard a good invariant merely because the old implementation lived in an Auth0-specific module.

---

# 25. Explicitly out of scope for this rewrite

Unless a milestone discovers a hard dependency, do not mix these into the serverless rewrite:

- payment portal/subscriptions;
- marketing site redesign;
- collaboration/real-time coauthoring;
- CRDT/Google-Docs-style simultaneous editing;
- mobile native applications;
- public launch;
- multi-region active/active;
- staging environment;
- Kubernetes/GKE;
- Cloud SQL merely because it is familiar;
- local Schema migration for unrelated features.

The payment portal will be easier to build after identity/entitlement boundaries are stable, but it is not part of this plan.

---

# 26. Decision rules for future models

When a future model encounters uncertainty, use these priorities in order:

1. **Do not damage local Breadmaker.**
2. **Do not lose or corrupt manuscript data.**
3. **Do not weaken authentication/authorization invariants.**
4. **Keep hosted correctness independent of process lifetime.**
5. **Prefer explicit interfaces over `if cloud:` branches spread throughout product code.**
6. **Prefer GCP-native managed primitives that scale to zero/usage when they fit semantics.**
7. **Do not put SQLite on Cloud Storage FUSE/NFS as the hosted database.**
8. **Do not introduce fixed monthly infrastructure merely to imitate a larger company.**
9. **Infrastructure changes are code-owned; secret payloads are the deliberate exception to IaC state, not to automation.**
10. **Do not move to the next milestone while the user's verifier is red.**
11. **Update the tracker continuously.**

---

# 27. Current external facts used by this plan

These are not timeless truths. Re-check official docs before implementation if a milestone occurs substantially later.

- Cloud Run scales services to zero by default when there is no traffic; request-based billing with min instances 0 has no idle instance charge.  
  https://docs.cloud.google.com/run/docs/about-instance-autoscaling  
  https://docs.cloud.google.com/run/docs/configuring/min-instances  
  https://docs.cloud.google.com/run/docs/configuring/billing-settings

- Cloud Run container filesystem is disposable; persistent state must be external.  
  https://docs.cloud.google.com/run/docs/overview/what-is-cloud-run

- Firebase Hosting can rewrite requests to Cloud Run and can serve custom domains.  
  https://firebase.google.com/docs/hosting/cloud-run

- Firestore supports atomic transactions/read-modify-write retries.  
  https://docs.cloud.google.com/firestore/native/docs/manage-data/transactions

- Cloud Storage supports generation/metageneration preconditions for race-safe conditional operations.  
  https://docs.cloud.google.com/storage/docs/request-preconditions

- Identity Platform supports TOTP MFA and recent reauthentication flows.  
  https://docs.cloud.google.com/identity-platform/docs/admin/enabling-totp-mfa  
  https://docs.cloud.google.com/identity-platform/docs/web/reauth

- Identity Platform documents Google and Apple web providers.  
  https://docs.cloud.google.com/identity-platform/docs/web/google  
  https://docs.cloud.google.com/identity-platform/docs/web/apple

- Identity Platform Tier-1 email/social MAU pricing currently gives the first 50,000 MAU at $0.  
  https://cloud.google.com/identity-platform/pricing

- Firebase Admin supports server-side session cookies and non-persistent client auth during exchange.  
  https://firebase.google.com/docs/auth/admin/manage-cookies

- Current Google/OpenTofu provider resources expose Identity Platform config including disabled user signup and TOTP provider configuration, but provider docs warn some sensitive values can be stored in raw state. Re-check before deciding which fields IaC owns.  
  https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/identity_platform_config

- Google provider currently exposes Firebase Hosting site/custom-domain resources; custom domain is beta/google-beta at plan creation.  
  https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/firebase_hosting_custom_domain

---

# 28. Handoff checklist for every future chat/model

Before coding:

1. Read `SERVERLESS_HOSTED_REWRITE_TRACKER.md` first.
2. Read this plan's current milestone.
3. Read both repo `AGENTS.md` files.
4. Read the owning domain docs/code map before implementation.
5. Confirm what the last user-run verifier reported.
6. If tracker and chat disagree, do not guess—inspect files and update tracker with evidence.
7. Do not run the verifier/tests yourself.

Before responding with a milestone package:

1. Update tracker.
2. Record exact application/cloud versions.
3. Record files/owners changed.
4. Record static checks actually performed.
5. Clearly say tests/verifier were not run by the model.
6. Give the user the package and exact verifier command(s).
7. Wait for user verification before marking milestone complete.

### M9C2B execution rehearsal checkpoint (10.3.76; awaiting user verification)

Bounded, restart-safe erase-intent and cursor mechanics are now modeled against *exact in-memory providers only*, isolated from hosted HTTP and real Google drivers. Production purge remains disabled pending Google missing-parent scans, a live cross-service write fence, conditional provider deletes, sharded manifests, backup completeness proof, restore drills and privileged IAM. Refer to `breadmaker/docs/HOSTED_DELETION_M9C2B.md`.


### M10A source checkpoint (2026-10-12)

Editor 10.5.0 / Cloud 10.4.0 initiates the user-requested new release line. The staging Terraform root now composes protected Firestore Native/GCS, separated web/backup IAM identities and a private Cloud Run v2 service instead of the undeployed VM/NAT/GFE infrastructure. Backups remain a hard-disabled job template until a real CLI, independently verified archive and Scheduler invocation exist. Firebase Hosting source routing is drafted only; no site/domain/DNS/Invoker granted. Full user verifier is pending. Production remains disabled, and normal IaC must never erase protected data. See `breadmaker-cloud/runbooks/M10A_STAGING_CLOUD_RUN.md`.


### M10D independent restore rehearsal (2026-10-12)
Staging gets an optional, digest-pinned, manual Cloud Run Job bound to an independent recovery-bucket-only viewer. A pinned hosted-v3 archive can be replayed into isolated in-memory state without Firestore; default job activation and automatic scheduling remain off. User-run conformance is a source gate, **not** live IAM/coverage/deployment proof. See `breadmaker-cloud/runbooks/M10D_ISOLATED_STAGING_RESTORE.md`.

### M10/M11 combined source checkpoint (Editor 10.5.5 / Cloud 10.4.6)

The current production root opts into a complete serverless data-plane module only
when `enable_data_plane=true` in a reviewed M12 apply. M11 owns two independent
PR checks and GitHub rulesets, guarded digest-based delivery and known-good
rollback. The current immutable base-image pin, deployment permissions, Hosting,
DNS, budgets, live backup coverage and IAM verification are deliberately M12
blockers. No private-alpha admission or resource creation is part of this bundle.


**M10/M11 corrective 10.4.7 (2026-10-12):** Cloud-only release/policy synchronization, HCL format normalization and strict whitespace-insensitive WIF gate test. Pending the operator full Cloud verifier. Editor 10.5.5 remains unchanged.

### M10/M11 Cloud 10.4.8 corrective status

Cloud-only correction for WIF default assertion and HCL list formatting; production resources remain disabled, GitHub rules not applied, M12 not deployed. User-run Cloud verification pending.
