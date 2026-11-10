> **Historical security journal.** Earlier entries predate the current M12 source-closure scope and may describe work that is no longer pending. See [M12 source closure](M12_SOURCE_CLOSURE.md) and [Security](../../SECURITY.md) for current status.

> **SOURCE-AUDIT FINDINGS REFERENCE.** For current verification status and unclosed security gates use `SERVERLESS_HOSTED_REWRITE_TRACKER.md` at the ZIP root. The findings below document source review, not a penetration test or SOC 2 certification.

## 2026-10-13 — M12E private Firestore Task/milestone encryption pilot (Editor 10.5.13 / Cloud 10.4.11)

Added hosted-only `HostedPrivateFirestoreFields` backed by existing AES-256-GCM/KMS envelope, explicitly injectable through hosted DomainStore and CloudProjectRepository for `task` and `task_milestone`. The user-defined record fields become ciphertext, operational IDs/timestamps/generation remain visible, tenant AAD binds project+entity+record. Normal hosted runtime still DOES NOT ENABLE encryption. Generic protected batch and project-package paths fail closed; authorized bounded list filtering occurs only after decryption. Eight targeted conformance cases authored and NOT run. No local SQLite/runtime/backups/GCP/Cloud files changed. `M12E_ENCRYPTED_FIRESTORE_HANDOFF.md` and coverage JSON document exact boundaries, unknown risks and next steps. Do not claim whole hosted data is encrypted. Editor 10.5.12 verification remained pending when M12E work began; Editor 10.5.13 verification, including both dependency audits, was still pending.

# Breadmaker — Milestone 12 security audit and hardening

**Audit date:** 2026-10-12  
**Candidate versions:** Editor 10.5.6; Cloud 10.4.9  
**Baseline:** operator-verified Editor 10.5.5 and Cloud 10.4.8  
**Scope:** 1,297-file baseline combined source, application/runtime/server, local secrets, hosted stores/API, browser UI, IAM/OpenTofu, recovery jobs, CI/CD, direct and hosted dependencies.  
**Method:** source inspection, attack-surface inventory, code pattern searches, historical conformance review, external security advisory lookup, narrow static syntax checks; **NO application tests, live service, dynamic penetration test, credential checks, or successful live dependency advisory scan**. Do not call this a third-party penetration test or SOC2 audit.

## Executive decision

**M12 is an offline code security review, not a security certification.** Candidate is **NOT APPROVED for public deployment or real manuscripts** until new verification, current dependency advisories, image scanner and M13 live access/restore/IAM checks are complete. No evidence of a reproducible cross-tenant disclosure was established by static review; that does **not** establish absence of one. The externally auditable deployment remains off.

## Confirmed source findings / corrective actions

| ID | Severity | Surface / source | Observation | M12 action / status |
|---|---|---|---|---|
| SEC-01 | **High — privacy** | `breadmaker/src/backend/diagnostics.py`, `log_event` | Hosted unexpected errors could write exception values, tracebacks and unconstrained context to Cloud Run stderr. A provider exception can include a token or manuscript fragment. | **Remediated in candidate:** hosted logs are structural IDs/class only; no `str(error)`, message, context or traceback. Local owner-controlled diagnostics preserve troubleshooting detail. New regression tests. |
| SEC-02 | **Medium — HTML injection** | `breadmaker/static/js/features/account/identity-platform.js`, TOTP UI | Provider-supplied MFA display names were inserted into `innerHTML`; client-generated enrollment secret also interpolated into HTML. CSP offers defense in depth but is not a substitute for escaping. | **Remediated in candidate:** dynamic values assigned with `textContent`. |
| SEC-03 | **Medium — redirect** | `identity-platform.js`, `safeReturn()` | Checking only leading `/` and not `//` did not rule out browser-normalized backslash network-path URLs such as `/\host`. | **Remediated in candidate:** reject backslashes/controls, normalize with `new URL`, require exact same-origin destination. |
| SEC-04 | **Medium — data echo** | `breadmaker/src/backend/http_boundary.py` | FastAPI/Pydantic validation details included `input` and nested `ctx` which may contain manuscript/token fields. | **Remediated in candidate:** hosted 422 response includes only bounded error `type` and `loc`; local API behavior unchanged. |
| SEC-05 | **High — supply-chain coverage gap** | `breadmaker/verify-release` and Editor/Cloud GitHub Actions | The existing release gate audits `requirements.txt`, but hosted-only Firestore, GCS and Firebase Admin packages in `requirements-hosted.txt` were **not part of that specific gate**. The deploy image scan is future-gated and cannot replace earlier coverage. | **Mitigated in candidate:** additional mandatory CI `pip-audit --strict -r requirements-hosted.txt` before publish credentials in both workflow copies and Editor PR gate. An independent operator command `breadmaker/audit-hosted-dependencies` enables the same check now, without waiting for GitHub deployment. The actual scan result remains **UNVERIFIED** due unavailable PyPI DNS; fail-closed until user CI runs. |
| SEC-06 | **Medium — reproducibility** | Hosted Dockerfiles + Python manifests | Direct Python versions are pinned; transitive wheels lack a fully hash-locked, reproducible artifact set. Docker base uses a versioned tag, not SHA256. | **OPEN/M13 precondition:** audited wheel lock/hashes, reviewed OS base digest, digest-pinned scanner, artifact scanning results, SBOM/provenance before deployment. Do not treat 2026 pinned versions alone as proof. |
| SEC-07 | **High — incomplete deletion guarantee** | M9 sandbox/operator deletion + archives | Cloud physical purge and external identity revocation are disabled. Recovery archives may outlive source project; arbitrary unclassified Firestore content is not proven restorable. | **OPEN deliberately:** do not advertise complete cloud deletion; keep irreversible endpoints and IAM disabled until live coverage, cross-provider fence, recovery and revocation evidence. |
| SEC-08 | **Medium — global collection-group reads** | `breadmaker/src/hosted/http_compat.py`, `_locate` | Nested entity resolution queries a global Firestore collection group and only then filters row paths for a known project. Authorized records are checked before return, but cross-tenant reads may increase cost/memory exposure under adversarial lookup rates. | **OPEN:** redesign as project-bounded lookup/index or scoped parent traversal and add cardinality limits before multi-tenant deployment. Not asserted as an observed customer-data leak. |
| SEC-09 | **Medium — distributed abuse controls** | `breadmaker/src/backend/request_pipeline.py` | Rate limits are per replica/in-memory; horizontally scaled Cloud Run needs upstream shared/global flood protection. | **OPEN/M13:** ingress quotas/WAF/cost budgets, negative load tests and alerts. |

## Defenses observed in source, not live-proven

- **Tenancy:** Firestore project membership checks are in specialized repositories and hosted product methods pass the authenticated user id. Project paths are opaque; unrecognized records fail closed in deletion discovery. M12 adds an adversarial second-user create/read/update/delete contract, **not run yet**. No dynamic browser/API cross-tenant probe or actual IAM simulation has been performed.
- **Authentication and sessions:** Identity Platform is authoritative; email is metadata rather than a join key; Secure/HttpOnly host-only cookies, same-origin mutation guards, provider cookie revocation and fresh proof for account-sensitive operations exist. External Google/Apple/provider policies need M13 configuration and end-to-end negative tests.
- **Storage:** Firestore and GCS client libraries use Application Default Credentials (no checked-in private service-account key); GCS bucket policy declares `public_access_prevention`, UBLA, versioning, soft-delete and retained recovery copies. Encryption is **Google-managed at rest, not client-side E2EE**. Cloud operators and sufficiently privileged services can read manuscript plaintext. Real GCP IAM and audit logs have not been inspected.
- **Local user-owned storage:** Manuscripts/SQLite are stored on disk by design. Backup OAuth/WebDAV credentials are AES-GCM sealed using a per-installation key in a separate owner-only file; this is not protection against compromise of the logged-in OS account. Local diagnostic logs may contain exception values; those remain on the user's own machine and warrant informed support-bundle handling.
- **Backups:** GCS archival writer is append-only and independently verifiable. It is *not* a universal archive of all Firestore account/system records; actual end-to-end restoration after deleting Firestore must be proved in M13. Local Schema 42 can't roundtrip every frozen Review/Canvas history.
- **CI/CD:** PR verification has read-only GitHub access. Production WIF restricts repo ID, owner ID, environment, ref and exact workflow refs. Rollback requires an approved digest; release is disabled. GitHub rulesets are source-defined but not applied; `known-good-digests.json` currently empty.
- **Parsing/HTTP:** Hosted request-body size enforcement, image signatures, zip/package limits, CSP, nosniff, frame blocking and origin controls exist; native image/document parsing remains a supply-chain and resource-exhaustion risk.

## Software supply-chain findings and verification limits

Direct production pins inspected: FastAPI 0.141.1, Starlette 1.7.0, Uvicorn 0.54.0, Pydantic 2.13.5, AnyIO 4.15.1, python-multipart 0.0.32, ReportLab 5.0.1, Pillow 12.3.0, cryptography 50.0.1, Authlib 1.8.0, JOSERFC 1.7.5, plus other direct packages. Hosted additions: google-cloud-firestore 2.34.0, google-cloud-storage 3.16.0, firebase-admin 7.7.0. External advisory checks identified Starlette advisories fixed before 1.7.0, cryptography GHSA-537c-gmf6-5ccf patched from 48.0.1, and Pillow CVE-2026-59200 fixed in 12.3.0. **These spot checks are not a dependency vulnerability clearance.**

Attempted a standalone `pip-audit` on `requirements-hosted.txt`, but DNS for `pypi.org` was unavailable in this tool environment (exit 2). Therefore there is **no successful current transitive vulnerability result to attach**, and no container SBOM or OS package scan. M12 acceptance requires the user's successful networked `cd breadmaker && ./audit-hosted-dependencies` result (also enforced later by CI). Docker base and full image scans remain M13 deployment prerequisites. Never invent a clean result.

## Privacy and user-trust statement required before hosted access

Breadmaker hosted is **not zero knowledge**. Manuscript content, annotations, reviewer notes and assets will be stored as server-readable data in Firestore/GCS; backups may persist independently after deletion according to retention policy. Google Cloud and authorized Breadmaker operators/services may access that plaintext within their privileges. Breadmaker must publish truthful retention, operator-access, incident-response and deletion policies before real user data is accepted. The local/offline Editor does not require hosted identity or GCP for core writing. No content monetization, AI-training or secondary disclosure guarantee can be inferred solely from these source files.

## M12 acceptance / M13 separation

**M12 (model-owned source audit):** all Editor/Cloud suites green on the security candidate; hosted dependency audit green (or precise advisory triage), static scripts/ZIP clean; high-severity source risks fixed or explicitly release-blocked; report retained. **Do not call M12 finished on static inspection alone.**

**M13 (operator-owned live deployment/security acceptance):** approve GCP billing/project; establish effective IAM and negative permissions; pin and scan Docker base/scanner/all delivered images; apply GitHub branch rules & guarded WIF; DNS/Hosting only by approval; build digest-pinned release; exercise two-user cross-tenant API tests, session/CSRF tests, DDoS controls, real GCS backup/readback/isolated restore, logs privacy probes, audit retention/deletion, alerting and rollback. **Do not admit private-alpha users yet.** Optional staging/autopromotion is deferred beyond M13.

## M12B continuation — 2026-10-12

Source candidate Editor 10.5.7 / Cloud 10.4.10 adds hosted-only AES-GCM/KMS groundwork and opt-in privacy-limited numeric metrics schema, plus project-scoped nested lookup replacing global collection-group reads in HTTP compatibility. These are **written but not dynamically tested**; encryption is **not activated**. SEC-08 global nested lookup has been changed but awaits user tests/performance evidence. SEC-09 distributed rate limiting, SEC-06 supply chain, live IAM and real restore still OPEN. Local/offline continues to require zero infrastructure. Full implementation and blockers in `M12B_PRIVACY_IMPLEMENTATION.md`.

## 2026-10-13 addition — release-gate coverage

Editor 10.5.8 adds fail-closed hosted-transitive vulnerability scanning directly to `breadmaker/verify-release`, after the existing core scan and before the test harness starts. `requirements-hosted.txt` includes KMS, Firestore, GCS and Firebase Admin dependencies. The scan requires internet access to resolve advisories and does NOT require a hosted deployment, cloud identity, GCP project, or installing these SDKs locally. **A green source-code test suite is not by itself vulnerability clearance**; owner must submit a green integrated dependency audit. Inactive application-layer encryption and unverified live IAM/recovery remain release blockers. Verification was pending at this historical checkpoint.

## 2026-10-13 — Editor verification and Cloud 10.4.11 correction

The operator reported the Editor 10.5.8 release gate fully green, with **core and hosted dependency vulnerability preflights** completing successfully before test execution, followed by 89 Unit, 1,308 Core/API, 94 current-browser, and 92 durable E2E tests passed (1 Core/API skipped). This is supply-chain evidence for the audited pinned Python requirements at scan time, **not** a live container/SBOM vulnerability scan or proof of SOC 2 compliance. Cloud 10.4.10 had 132 passes and one version-regression failure: `test_repository_contract.py` still asserted 10.4.8. The 10.4.11 source patch updates two release consistency contracts plus `VERSION`, README, and identity policy version. No deployed resources or authorization policies change, and the user must verify Cloud 10.4.11.

## M12C encrypted private object pilot — source checkpoint (2026-10-13)

Candidate Editor 10.5.9 adds a non-activated encrypted GCS ObjectStore publication/readback boundary (authenticated AES-256-GCM, context-bound record/operation, opaque object paths, ciphertext-only integrity metadata, create-only generation and verified replay). Existing hosted Firestore/private models, asset publishers, exports and archives remain NOT_INTEGRATED. This should not be interpreted as current hosted encryption coverage. Per-record authorization, IAM, key rotation/retention, live encrypted restore, and full data inventory are pending. The first complete integration should be measured with persisted-byte and cross-tenant failure tests. Local offline operation remains unaffected. See `M12C_ENCRYPTED_OBJECT_HANDOFF.md`. No suite results or live deployment were recorded at this checkpoint.

## 2026-10-13 M12D partial encrypted-domain integration

An explicitly injected encrypted GCS writer now participates in hosted manuscript-block CRUD and atomic chapter replacements through canonical membership checks and generation-pinned Firestore pointers. Encrypted-mode retries use ciphertext-pointer fingerprints; package import/export rejects the experimental mode rather than writing plaintext. This is **source work only**, NOT verified yet and **NOT active** in the ordinary hosted runtime. Firestore metadata, other objects, archives and non-block families still lack encryption. Do not classify M12 as providing universal manuscript confidentiality. Tests and scans were not run at this checkpoint. See `M12D_HOSTED_BLOCK_INTEGRATION.md`.

## 2026-10-13 — M12D verification correction

The owner ran M12D Editor 10.5.10: 1,593 passed, 7 failed, 1 skipped. All seven failures shared `NameError: encoded is not defined`, affecting six inherited M8B chapter-replacement cases and one new encrypted replacement case. Cloud 10.4.11 is owner-confirmed fully green (133/133), and its files remain unchanged. The original `chapter_mutations.py` M12D edit accidentally omitted `structured, encoded = hybrid` in the `hybrid is not None` branch; 10.5.11 restores that tuple unpacking before immutable publication, preserving both the plain and opt-in encrypted paths. One focused AST guard `tests/test_v10511_chapter_hybrid_contract.py` is added; the existing runtime tests remain unchanged. Editor verification was pending at this checkpoint. Local runtime/accountless SQLite/backup files and Cloud remain unchanged; hosted encryption remains opt-in, incomplete, and disabled in normal runtime. No GCP deployment, KMS key, hosted telemetry or destructive deletion has been activated.

## 2026-10-13 verification follow-up — Editor 10.5.12

Editor 10.5.11 eliminated all seven `encoded` NameError regressions, but the owner still observed two 10-second setup timeouts in cold-load API modules and one durable E2E failure creating second draft milestone. Editor 10.5.12 changes the existing per-module prewarm selection and guards stale `requestAnimationFrame` description focus when the user is typing another milestone. No 10s/15s timeout budgets are extended. No hosted encryption switch, storage driver or Cloud infrastructure changed; source/contracts are NOT verified until the owner reruns the Editor suite.

## 2026-10-14 — Security firewall retention / 10.5.14 corrective

Editor 10.5.13 user verification: 1,608 passed / 3 failed / 1 skipped, all three failures only from fixed SHA-256 expectations for the authorized 10.5.12 two-module verifier prewarm. The 10.5.14 corrective leaves `scripts/verify_release.py`, `tests/conftest.py`, all runtime, storage, IAM, and Cloud files unchanged, updating exactly three old expected hashes after comparing to 10.5.11/10.5.12 sources. The firewall continues to compare exact SHA-256 digests for all protected files. This is **not** proof of fully encrypted hosted storage, successful attack simulation, live tenant isolation or compliance. Full Editor user verification is pending.

## Source-only operational inventory preparation (2026-11-08)

`hosted.security.inventory` is a separate, default-disabled producer candidate.
It is not wired into local or hosted runtime, has no endpoint, scheduler, client
creation, environment switch, persistence, or Admin transport. No live provider
query, IAM change, deployment, or collection activation accompanies this source.
The existing user-scoped `DailyUsageMetrics` validation-only module is unchanged.

The versioned `cloud_operational_inventory_v1` definition permits exactly two
provider COUNT aggregates: top-level `users` where `status == active`, and
top-level `projects` with the same filter. These are persisted application
accounts and current published project roots. They are not distinct people,
online/concurrent users, lifetime creations, or project counts joined against
owner eligibility. Missing status is excluded, even where legacy runtime paths
treat it as active. Staged, deleted and erasure-pending roots do not match. A
successful zero is zero; unavailable, incomplete, stale, or failed data is null.

The compact JSON contract contains only schema/source constants, UTC seconds,
fixed coverage enums, and `active_cloud_accounts` / `current_cloud_projects`
integer-or-null values. Counts must be genuine nonnegative integers at most
9,007,199,254,740,991; no IDs, emails, titles, provider exception messages, or
manuscript fields enter the payload. Each SDK COUNT stream is consumed at most
two result rows, must contain exactly one `total` aggregate, and is closed.
The pinned Firestore 2.34.0 SDK decodes COUNT zero as `0.0`; only that exact
SDK representation is normalized to integer zero at the fixed COUNT boundary.
All other floats and all non-integer snapshot/wire counts remain invalid.
Queries have a 20-second RPC timeout and no retry. An overall 60-second budget
and 900-second export freshness ceiling fail closed. Provider read times must
fall inside the UTC observation window; clock reversal fails structurally.

The two aggregate queries run sequentially, not in a transaction. Coverage is
explicitly `non_atomic`; `complete` means both narrow queries completed, not
that they describe one instant or all product usage. Any one-query failure
suppresses both counts. No document download, collection-group query, content
crawl, mutation, or fallback to cached counts is supported.

Activation remains blocked pending approved privacy notices/terms, permission
review, provider/index verification, minimum required IAM, an authenticated
aggregate-only publication/read boundary, and operator validation. An injected
client with `enabled=True` is only a mechanical gate, not approval to collect.
Concurrency requires separately approved account-linked activity semantics;
canvases require active-parent eligibility; words and characters require trusted
save-time counters rather than manuscript traversal. Those metrics remain
unavailable. Synthetic focused tests are added for owner/CI execution; no agent
suite or live integration check was run for this source preparation.

## Unconnected canvas projection source (2026-11-10)

`hosted.security.canvas_projection` adds a pure private count reducer, with no
runtime import, store, query, writer, account identifier, manuscript read, or
telemetry hook. Existing saves, imports, project mutations and the two-field
Admin inventory schema are unchanged. No projection is collected or persisted.

The definition is current canvas-root existence within a project, excluding
nodes, edges, versions and lifetime creation events. A projection records a
private project scope, exact project generation and integer count or unknown.
Trusted complete transaction before/after existence images produce the delta;
updates to an existing canvas contribute zero. Duplicate roots, non-unit
transitions, replay, out-of-order or foreign-project application, underflow and
overflow fail closed. Unknown baselines remain unknown through every mutation.

A new zero baseline requires the `TrustedEmptyProjectCreation` server-only proof
interface. Its future implementation must verify authorized empty creation and
absence of a prior projection in the same transaction. Generation one, a client
Boolean or a request payload is not proof: imports can publish nonempty roots at
generation one. No production proof implementation, deserializer, reseed API,
nonempty initialization or backfill is included. Python types do not establish
authority; request data must never construct this evidence or private state.
The proof interface alone is not a security mechanism. Verifier failures become
a fixed structural error; future composition must also sanitize errors and
never log provider payloads.

The existing integration points are `HostedDomainStore.create` / `delete` and
`_advance_project` in `src/hosted/domain_store.py`, plus the explicit generation
advance in `src/hosted/chapter_mutations.py` and canvas-version activation in
`src/hosted/canvas_versions.py`. Future integration must cover every root
advance, including unrelated edits, Undo/Redo, import, restore and account/project
lifecycle paths. It must write the projection atomically with the original
mutation and exact generation CAS. This module does not install those hooks.
Unsupported or skipped transitions must invalidate coverage, never guess a delta.

`eligible_canvas_count` emits only an integer or null, and only an explicit
active root of the same project and exact generation makes a known count
eligible. Deleted, erased, staging, missing and stale observations yield null,
not a fabricated zero. A future fleet aggregator must separately prove complete
active-parent coverage; summing only known projections is not a total.

Words and characters remain unavailable pending canonical count definitions and
coverage across save, import and Undo paths. Presence remains unavailable pending
approved privacy and activity semantics. This source slice adds no permission,
credential, runtime setting, live provider call or deployment authorization.

Focused synthetic verification on 2026-11-10 uses the current repository policy:
commit `7381c8e` removed the older AGENTS milestone handoff; current
`CONTRIBUTING.md` and `docs/TESTING.md` permit focused test modules. The inventory
and canvas-projection unit modules were executed with the existing item
watchdogs. No full release verifier or live provider check was executed.

## Disabled latest-only inventory publication source (2026-11-10)

`hosted.security.inventory_publication` now supplies an explicit one-shot,
default-disabled coordinator for the existing two-count contract. It reads the
current aggregate object, collects a new observation, requires strictly newer
`as_of`, then writes with the exact observed object-generation precondition.
Absence requires create-only generation zero. A conflict is returned without a
retry. Equal-second observations are deliberately not newer. Fresh unknown or
partial observations replace earlier good values with nulls; fatal read/clock
failures do not publish a fabricated observation, so the previous object can only
remain usable until the independent Admin freshness ceiling expires.

`hosted.security.inventory_gcs` is a separate uncomposed SDK adapter. It accepts
an injected bucket matching the reviewed `breadmaker-admin-usage-*` convention
and standard storage endpoint, never credentials, client paths, URLs or object
names. Only `cloud-usage/v1/latest.json` is addressable. The naming/endpoint
checks do not prove resource isolation or authorization. No client is constructed
and no ADC or environment configuration is read by these modules.

Reads verify object size at most 16KiB, pin the download to the observed
generation, request a bounded raw range, check byte length and provider MD5
transport integrity, and validate the closed numeric-only schema. Uploads use
one bounded multipart request, a fixed no-store cache policy, no custom metadata,
a server-checked MD5 transport checksum and exact generation CAS. Every SDK RPC
has an explicit remaining timeout and `retry=None`. The fixed MD5 is transport
integrity, not an authentication mechanism or a manuscript-content identifier.
Provider payloads and exception messages are never logged or surfaced. A write
that may have succeeded before a transport failure returns uncertain, not a
retry or a claim of success. Callback deadlines are cooperative; no background
thread, polling loop or scheduler is installed.

There is no separate deletion, listing, historic-object or blanket cleanup API.
However, GCS overwrite requires delete/overwrite permission semantics even when
no separate delete request is made. A single live object name does not establish
retention: bucket versioning, soft delete, retention policy, lifecycle settings,
minimum IAM, keyless runtime binding, approved privacy notices, publication
cadence and operator validation remain explicit activation gates. No bucket,
credential, permission, runtime hook, production read/write or deployment was
created or enabled by this source change. The existing two-count wire schema and
unsupported status of words, characters, canvases and concurrency are unchanged.

The focused inventory, canvas projection and publication unit modules pass 148
synthetic cases. `scripts/check_inventory_admin_contract.py` also exercises the
actual producer and coordinator through an in-memory latest object and the actual
Admin HTTP reader/parser at commit
`eb090d887613ee2d4502045914630c632f4f2a5c`, with an in-memory HTTP opener and a
synthetic token. Counts, true zero, unknown invalidation and expiry pass. Run it
with explicit `--admin-source` and `--expected-admin-head` arguments; the harness
rejects a dirty or mismatched Admin checkout. No real credentials, HTTP calls or
provider mutations are involved. This is source-level contract evidence, not
live SDK, IAM, retention, deployment or end-to-end production acceptance.
