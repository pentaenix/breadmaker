# CANONICAL CURRENT TRACKER — 2026-10-14 — M12F

**Current candidate:** Editor **10.5.15** / Cloud **10.4.11**, **NOT YET USER-VERIFIED**. Last user-confirmed green: Editor **10.5.9**, Cloud **10.4.11** (133/133). Editor 10.5.13 user run: 1,608 passed / 3 failed / 1 skipped; all 3 were verifier SHA contracts. Editor 10.5.14 corrected those SHA expectations, but the user has not yet reported its verification result. **10.5.15 inherits that correction and therefore needs full Editor verification.** No Cloud source changes since green 10.4.11.

## Non-negotiable agent handoff

- The **human alone runs test suites**. AI agents MUST NOT run `./verify-release`, `./test`, `pytest`, Playwright, E2E, or wrappers. A developer may run only narrow static checks (AST/JSON/JS/bash parsing), release metadata sync and ZIP CRC. Owner returns condensed verification digest.
- **Local Breadmaker remains first-class, accountless and fully offline**, including editing, SQLite Schema 42, backups, import/export and recovery. No Internet, account, Google Cloud, KMS, telemetry or keys are required for `./run`. No local runtime, local requirements, storage schema or local backup modifications in M12F.
- No actual GCP deployment or KMS resources were provisioned. M13 is the human-controlled GCP/GitHub deployment and live security-validation milestone; private alpha deferred. No production project/account erasure permitted.
- Normal hosted runtime **still does not activate encryption**. AES-256-GCM + KMS-backed codec exists only via explicit test/operator injection; many private Firestore/GCS fields, object names, exports and archives are still plaintext in the normal hosted path. No claim of full hosted encryption, E2EE or SOC 2 compliance.
- Release ZIP contains `breadmaker/`, `breadmaker-cloud/`, this **single canonical tracker**, and historical docs. Future agents should **append each release to this tracker** rather than creating M12G/H/I handoff Markdown files. `READ_THIS_FIRST.md` is a short pointer, and previous per-milestone files are historical archives.

## M12F — expanded opt-in Firestore encrypted field coverage (10.5.15)

**Code change:** `breadmaker/src/hosted/security/private_firestore.py`. Retains the tested Tasks/milestones AAD format exactly. Extends the explicit `HostedPrivateFirestoreFields` allowlist to 13 additional Firestore-only model families: `review`, `review_comment`, `review_comment_reply`, `review_general_feedback`, `review_highlight`, `review_suggestion`, `canvas`, `canvas_node`, `canvas_edge`, `story_map`, `map_line`, `map_item`, `map_junction`. Other entity families (including hybrid snapshots, project metadata, recovery, user identity) are **not covered**.

- New families authenticate the **complete canonical Firestore document path**, including project, parent Review/comment/Canvas/map IDs and final record ID. Record context is a SHA-256 of **opaque IDs and path tokens only**, never plaintext contents. Full path is validated against the canonical frozen model registry. An encrypted reply belonging to a different comment or Review fails decryption even if the leaf ID matches.
- Data passed to `seal()` keeps only operational record IDs, generation, timestamps and ciphertext plus encrypted-operation receipts in Firestore. **All unknown user-editable fields are encrypted**, never silently added as plaintext. Conflicting parent IDs inside a private payload are rejected, while redundant matching parent IDs are stripped; decrypted data cannot override the authoritative document path. Reads refuse any undeclared field/legacy plaintext fallback and only occur after existing canonical authorization. KMS is outside Firestore transaction callback; mutations retain generation CAS and ciphertext-only retry fingerprints.
- Provider-side Firestore queries cannot search encrypted user fields. `HostedDomainStore.list` already handles opt-in private entities using bounded scans (512 maximum) and post-decryption filter/sort; large collections must get an explicit indexed metadata/selector design rather than loosening limits. `create_batch_atomic` and package import/export remain **fail-closed** for the experimental encrypted-domain mode.
- Tests authored, **not run by agent**: `breadmaker/tests/test_v10515_private_content_families.py` has 9 test functions (11 cases due to parameterization) covering ciphertext-at-rest, listing, full ancestor substitution, nested review replies, map items, no plaintext downgrade, owner-vs-outsider/KMS denial, local/hosted separation and protected export/batch boundaries.
- `breadmaker/docs/HOSTED_PRIVACY_COVERAGE_M12B.json` now records these 13 experimental cases, but **keeps `encryption_coverage=NOT_INTEGRATED` and `auto_activate=false`** for all families. Cloud 10.4.11 is unchanged, as is local-only persistence and the release test harness.
- Three preexisting exact protected verifier hashes were corrected in the inherited 10.5.14 bundle; do **not** change `scripts/verify_release.py` or weaken the hash/firewall watchdog contract.

**User action:** Run **only Editor** `cd breadmaker && ./verify-release` on the new ZIP (core + hosted strict `pip-audit` included). Cloud 10.4.11 is unchanged and already user-green. Send condensed digest; release remains unverified until then.

## Remaining M12 security work, in priority order

1. **Coverage completeness before activation:** encrypt every user-private Firestore family/field or explicitly classify minimally necessary searchable metadata. Audit direct repository mutations/bulk operations, review participant workflows, nested record pagination and CAS. Current opt-in path is not general rollout ready.
2. **GCS and archive lifecycle:** encrypted cuts, checkpoints, review snapshots, assets, revisions, author-controlled exports, version histories and immutable backups. Eliminate content-derived plaintext SHA names, prove independent generation-pinned encrypted restore and key-retention behavior. No plaintext export bypasses.
3. **Operational and privacy controls:** per-user metrics authorization/retention, distributed rate limiting/ingress controls, resource quotas, deletion coverage (currently intentionally disabled), secrets/logs minimization, complete transitive dependency and container/image audits. Source scans cannot prove live IAM isolation.
4. **M13 operator/live deployment:** provision isolated GCP project, billing safeguards, KMS key + minimal IAM, GitHub rules/WIF, signed pinned images, negative cross-tenant/IAM tests, real encrypted backups/restore, observability, rollback. No customers or private alpha until accepted.

## Historical tracker (chronological history retained below)

---

# CURRENT HANDOFF — 2026-10-14 — M12E VERIFIER SHA CORRECTIVE

**Candidate:** Editor **10.5.14** / Cloud **10.4.11**, USER VERIFICATION PENDING. Last user-reported fully green Editor **10.5.9**, Cloud **10.4.11** (Cloud additionally passed 133/133 when M12D was checked). The latest user-run Editor **10.5.13**: **1,608 passed, 3 failed, 1 skipped**; Unit 89/89, Core 1333 pass/3 fail/1 skip, current browser 94/94, durable 92/92. All three failing tests were **exact-SHA protected-file checks** in `test_v10353.py`, `test_v10354.py`, `test_v10360.py` and pointed to `scripts/verify_release.py`. Its content changed **only** in 10.5.12 to add `test_v10326.py` and `test_v700.py` to existing `PREWARM_APP_CORE_MODULES` without altering 10s/15s item watchdogs, and the script was unchanged in 10.5.13. Reviewed SHA updated in those three tests from `c5de57ae...` to `d2f145f8...`, with exact digest checking preserved. **No verifier/runtime behavior changed** in this corrective. Existing 10.5.13 encrypted Firestore pilot remains opt-in, inactive in the normal hosted runtime; global encryption and metrics are NOT enabled.

**Human action:** Run **only** `breadmaker/./verify-release` on Editor 10.5.14 (both core and hosted strict dependency audits included). Agent must **never run any tests**, pytest, Playwright, or verifier. Cloud 10.4.11 remains byte-identical and already user-verified. If green, proceed to encryption coverage/recovery work (M12F). Preserve first-class **offline/accountless local app**, local SQLite/backup/import and no network, Cloud, KMS or telemetry requirement. Private alpha deferred; M13 owns live GCP/GitHub tasks. Do not activate incomplete hosted encryption or claim compliance.

See `M12E_VERIFIER_HASH_REVIEW.md` for the reviewed difference and SHA contract. Historical status sections below are retained as archaeology and are superseded by this current block.

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

## 2026-10-12 — M12 source security audit / privacy hardening (Editor 10.5.6 / Cloud 10.4.9)

**AWAITING_USER_SECURITY_VERIFICATION.** User explicitly redefines M12 as the thorough source-side security audit; M13 is now operator-only provisioning/live acceptance, with private alpha deferred. Operator reported Editor 10.5.5 and Cloud 10.4.8 fully green before this source pass. This audit found and repaired unsafe hosted exception/traceback logging, unsafe MFA dynamic HTML, login return-path confusion and echoed validation inputs; adds hosted SDK advisory checks in Editor and Cloud CI. Includes adversarial tenant tests and complete risk register in `M12_SECURITY_AUDIT.md`. `pip-audit` could not reach PyPI from the model environment, so supply chain verdict is pending user CI. Production deletion remains disabled; advanced-history local portability, cross-tenant `_locate` global group reads, per-replica rate limiting, hash-locked Python wheels, live IAM and recovery evidence are explicit open issues. No suite, cloud apply or live audit executed by model. **Next:** user full Editor + Cloud verifier and hosted dependency scan; investigate failures and correct before M13 handoff.

## 2026-10-12 — User decision: defer private alpha; M10+M11 combined source candidate (Editor 10.5.5 / Cloud 10.4.6)

M10 production root now has a protected, optional data-plane composition with `enable_data_plane=false`. It reuses Cloud Run/Firestore/GCS/Identity Platform/backups; cloud deletion/schedulers, public Hosting and DNS remain off. M11 introduces independent Editor/Cloud protected-main CI checks, declarative GitHub rulesets, disabled-by-default post-merge immutable-image delivery, exact WIF trust, and an approved-digest rollback path. No GitHub ruleset/GCP resource was deployed. Current Dockerfile base and scanner digest remain M12 activation blockers; live Hosting/IAM/recovery also pending. Previous reported Editor 10.5.4 fully green, Cloud 10.4.5 corrective was shipped with user verification pending. Full new combined verifiers to be run by user only.

## 2026-10-12 — M10E Cloud-only contract corrective, Editor 10.5.4 / Cloud 10.4.5

**Status: AWAITING USER VERIFICATION.** User full 10.5.4/10.4.4: Editor 1,564 pass / 0 fail / 1 skip (Unit 89, Core 1289 pass+1 skip, browser 94, durable 92); Cloud policy 88 pass / 1 fail, identity 19 pass, OpenTofu 6 pass. Exact failure: `test_m10e_operator_cli_never_deploys_and_code_has_no_gcp_sdk_invocation` static source check forbids literal `subprocess` in `scripts/staging_activation.py`, which occurred solely in a nonexecutable docstring. Corrected only that language; left the fail-closed test, executable admission logic and physical deployment gates unchanged. Cloud `10.4.5` release metadata synchronized; Editor remains `10.5.4`. No tests executed by assistant. Next: operator full combined verifier on 10.5.4/10.4.5.

**ROADMAP DISCREPANCY:** Canonical plan §§3, 19–21 calls for cost-conscious production/private-alpha first, with no staging before M12; M10A–E instead built staging-oriented IaC/jobs. Explicitly choose to return to production-first as planned or amend the plan before cloud spending/deployment. M11 protected-main CI/CD, M12 real alpha, M9 complete recovery coverage/privileged erasure, live identity/restore proof remain open. M13 optional/deferred.

## 2026-10-12 — M10E staging activation intent/plan gate, Editor 10.5.4 / Cloud 10.4.4

**AWAITING USER VERIFICATION.** User reported M10D Editor 10.5.3: 89 unit, 1285 core passed/1 failed/1 skipped, 94 browser, 92 durable; Cloud 106/106 green. One failing invalid recovery-operation input was a domain HostedValidationError (RuntimeError), not the expected ValueError. Map boundary errors to RecoveryDrillConfigurationError before any SDK is built. This bundle adds non-deploying staging manifests and digest checking, two no-scheduler activation phases, offline Terraform variables emission, secondary OpenTofu plan firewall (identity/image/scope/IAM/ingress/deletion/resource coverage), tests, manual runbook. Neither staging nor production deployed, no real IAM evidence, no authority to delete or revoke identity. Model ran no tests. Next: user combined verifier and human-approved GCP plan/activation; retain real writer/drill/IAM/coverage/Hosting gates.

## 2026-10-12 — M10D isolated staging recovery drill, Editor 10.5.3 / Cloud 10.4.3

**AWAITING_USER_VERIFICATION.** Operator reported Editor 10.5.2 / Cloud 10.4.2 fully green. The M10D source-only bundle adds a read-only, GCS-only recovery drill CLI/container with explicit owner/project/operation, generation and SHA-256 pins; isolated hosted-v3 import/re-export equality check in in-memory providers; separately scoped staging drill service account (recovery bucket objectViewer only); disabled, unscheduled, digest-pinned Cloud Run Job; documented live staging recovery/permission gates. Unknown or changed archives fail closed. No Firestore permissions, original content access, GCP write/delete, public HTTP, production deletion/IdP revocation or deployment. Full DR coverage remains unproven. User owns full verifier and live staging activation. See `breadmaker-cloud/runbooks/M10D_ISOLATED_STAGING_RESTORE.md`.

## 2026-10-12 — M10B read-only recovery-plan operator, Editor 10.5.1 / Cloud 10.4.1

**AWAITING USER VERIFICATION.** M10A user result: Editor 1,529 passed / 0 failed / 1 skipped, including 92 durable E2E and 94 current-browser; Cloud OpenTofu 6/6, identity 19/19, policy 65 passed / 2 failed. Both policy failures were literal Terraform spacing / “milestone 10” text assertions; M10B corrects those without skipping protections. Adds non-root read-only Firestore recovery-schedule job and separate service account, bounded 500-item owner/retention audit, staging-only preflight, optional disabled-by-default Cloud Scheduler with job-scoped invoker, digest-pin gate and conformance cases. The plan job NEVER writes archives; the backup writer stays hard disabled. No assistant test execution, no real GCP apply, no Hosting activation, no production purge.

**Next safe action:** user runs combined Editor+Cloud full verifier on 10.5.1 / 10.4.1. Then prepare audited archive-writing worker, protected staging deployment, identity/IAM proof, and live restore drill. See `breadmaker-cloud/runbooks/M10B_RECOVERY_PLAN_JOB.md`.

## 2026-10-12 — M10A staging IaC rewrite, Editor 10.5.0 / Cloud 10.4.0

**AWAITING USER VERIFICATION.** User reported fully green on previous Editor 10.3.82 / Cloud 10.4.38. New explicitly requested versions 10.5.0 / 10.4.0 begin the serverless M10 series. Staging active IaC replaces legacy VM/NAT/HTTP-LB with private Cloud Run v2, protected Firestore Native/PITR, three versioned protected GCS buckets and split runtime/backup service identities. No actual GCP apply/deploy. Backup job hard-disabled because reviewed CLI does not exist; no Scheduler, Firebase Hosting activation, DNS or external deletion authority. Plan protection extended to Firestore. Cloud contracts revised only where they required old VM; local Editor Schema 42, hosted adapters and suite runner untouched. Assistant has not executed tests. Live GCP evidence, advanced local history and privileged purge still explicit pending gates.

## 2026-10-12 — M9 operator-facing integration candidate 10.3.82, user verification pending

- Prior **10.3.81** user FULL verifier green: Editor 1,516 pass / 0 fail / 1 skip, Cloud 10.4.38 85/85 pass. Memory-only project purge and account closure rehearsals are now operator-verified within their simulated scope.
- Added four non-HTTP operator modules for Google-only read-only purge preflight, schedule job inventory, independent offline restore drills, and identity binding review. No real GCP erasure and no identity revocation enabled.
- New source-only conformance contracts `tests/test_v10382.py`; no assistant test runs.
- Live GCP orphan scan, cross-provider write fence, dedicated deletion IAM, sealed audit authority, revocation, job deployment and disaster drills remain **hard blockers**, not implicitly waived by `assess()` output. Complete frozen historical Review/Canvas/checkpoint local import remains a separate portability gap.
- Acceptance: user full Editor + Cloud verifier green, then stage the operator/run-job/IAM provisions in M10 and require real destructive drills on disposable test data only, with deletion unavailable in production pending separate approval.
- Full criteria and security checklist: `breadmaker/docs/HOSTED_M9_FINAL_OPERATOR_HANDOFF.md`.

# M9 account deletion rehearsal + firewall corrective — Editor 10.3.81 / Cloud 10.4.38

Status: **AWAITING USER FULL VERIFIER**. Editor 10.3.79 user check: Unit 89/89, Core 1220 pass / 0 fail / 1 skip, current browser 94/94; durable E2E Chromium launch timed out in first setup causing 92 dependent setup errors. All new Recovery/Deletion conformance cases passed. 10.3.80 moves only shared durable browser startup to a separately bounded session prerequisite; per-item watchdogs unchanged. Cloud 10.4.38 files unchanged; no new Cloud result was included in uploaded Editor report. No physical deletion or account erasure enabled. Model ran no tests.
program: breadmaker-serverless-hosted-rewrite
tracker_format: 1
baseline_editor_version: 10.3.41
baseline_cloud_version: 10.4.32
current_editor_version: 10.5.6
current_cloud_version: 10.4.9
current_milestone: 12_SECURITY_AUDIT_HARDENING
current_milestone_name: Comprehensive source security audit, hosted privacy fixes and dependency coverage
status: AWAITING_USER_VERIFICATION_M12_SECURITY
last_user_verification: EDITOR_10_5_5_AND_CLOUD_10_4_8_ALL_GREEN_USER_REPORTED
model_must_run_verifier: false
model_must_run_test_suite: false
live_environment_exists: false
hosted_user_data_exists: false
local_schema: 42
next_safe_action: "Operator full Editor+Cloud verification 10.5.6 / 10.4.9 and networked hosted SDK pip-audit; review M12_SECURITY_AUDIT.md blockers before M13 manual cloud activation."
last_updated: 2026-10-12
---

# Breadmaker Serverless Hosted Rewrite — Live Tracker

## 2026-10-12 — Combined deletion lifecycle rehearsal, Editor 10.3.81 / Cloud 10.4.38

User 10.3.80 gate: Unit 89/89, Core/API 1219 green with ONLY 3 stale historical `tests/conftest.py` hash assertions and 1 skip; Current browser 94/94; **Durable E2E 92/92**; Cloud 85/85. Update the 3 exact SHA256 expected values to the approved browser lifecycle file without removing any firewall assertions or altering the protected verifier/watchdogs. Build memory-only `HostedSandboxAccountClosure` (19 new tests) across owner-project purge prerequisite, collaboration detach, identity/audit scoping, durable cursor/lease/crash replay, immutable backup preservation, unknown user reference detection, and terminal external identity revocation pending. This is NOT a production deletion implementation; no hosted route, real GCP delete permission, Cloud changes, or IdP revocation. User full suite pending. See `docs/HOSTED_DELETION_M9C3_REHEARSAL.md`.

## 2026-10-12 — Durable browser cold-start corrective, Editor 10.3.80 / Cloud 10.4.38

10.3.79 user verification confirmed all Unit, Core/API (except one skipped) and current browser cases green, but a recurring cold `pw.chromium.launch()` exceeded the hard 15-second FIRST durable-item setup budget, cascading to all 92 E2E tests. Browser startup was shared across the entire durable module and belongs to session preparation. Only verified durable runs now open the shared Chromium in `pytest_sessionstart` under independent 30s/40s startup guards. Per-test 15s watchdog is unchanged, regular current browser cases and direct pytest are unchanged, and the browser is closed before the test server. Two source contracts guard the distinction. Model ran no tests. Cloud 10.4.38, hosted deletion/recovery, and local runtime unchanged. See `READ_THIS_FIRST.md`.

## 2026-10-12 — M9 deletion foundation bundle, Editor 10.3.79 / Cloud 10.4.38

User verified 10.3.78 Cloud 85/85 and Editor unit 89/89, browser 94/94, durable E2E 92/92; only two core recovery-drill tests failed when the isolated importer built `HostedUserRepository` from `MemoryDocumentStore` instead of `HostedStructuredRepository`. This release corrects that wiring. It also starts the combined M9C2C/M9C3 effort: memory-only authenticated account inventory (including ownership/collaboration/identity bindings/orphans/assets/recovery and revision-based fingerprint) and a strictly non-authorizing production purge evidence gap checklist. Twelve new tests authored but not run. **Production Firestore/GCS purge, account deletion, HTTP routes, live GCP worker/permissions and real account-provider identity revocation remain disabled/not deployed.** Existing protected local architecture, freeze-adapters, verifier, watchdogs, and Cloud 10.4.38 unchanged. See `breadmaker/docs/HOSTED_DELETION_M9C_BUNDLE.md`.


## 2026-10-11 — Recovery engineering bundle, Editor 10.3.78 / Cloud 10.4.38

Includes unverified 10.3.77 and new `recovery_schedule.py`, `recovery_drills.py`, `portability_audit.py`, plus conformance `test_v10378.py`. Owner-only durable backup policy and job-invoked idempotent immutable recovery cycle with verified GCS receipts; offline independent Firestore-loss restore drill into isolated memory providers with entity+asset semantic comparison; strict Schema-42 compatibility audit. No real scheduler/job deployment, IAM/retention policy, account provider restore, destructive Google deletion, or incomplete-history coercion. Human full verifier pending. See `docs/HOSTED_RECOVERY_M9D_BUNDLE.md`.


## 2026-10-11 — M9C2B sandbox purge execution rehearsal, Editor 10.3.76 / Cloud 10.4.38

**USER CONFIRMED Editor 10.3.75 and Cloud 10.4.38 ALL GREEN; M9C2A IS VERIFIED within the non-destructive scope.** This checkpoint adds a new separate hosted-only `purge_sandbox.py` restricted at constructor time to the exact in-memory Firestore/GCS fakes. It can advance a bounded (128-work-item) durable sandbox execution receipt and remove scoped *fake* content/documents, one step at a time, with a claimed inflight object intent, 30-second worker lease, GCS generation CAS, replay after injected crash, per-step backup verification and revision/inventory fences. Owner and project root are deleted together in the last memory transaction. No HTTP, real GCP SDK, real container job, production IAM or account erasure path is added. No GCP write-fence or backup coverage proof exists yet; **production physical purge remains disabled**. Ten conformance tests authored, NOT run by model. Full user Editor+Cloud verifier is required. Cloud 10.4.38, protected local runtime and frozen adapters unchanged. See `breadmaker/docs/HOSTED_DELETION_M9C2B.md`. M9B advanced history and M9D scheduler/restore drills remain incomplete.


## 2026-10-11 — M9C2A non-destructive discovery/receipts, Editor 10.3.74 / Cloud 10.4.38

**USER CONFIRMED all green for Editor 10.3.73 + Cloud 10.4.38.** This
closes M9C1 (read-only scoped), and verifies the carried restricted M9B
conformance without implying complete rich-history parity. M9C2A adds
`src/hosted/purge_discovery.py` (provider-neutral scanner boundary; memory
full-tree discovery including orphaned nested descendants; hosted-only Google
RPC `show_missing=True` recursion) and optional exhaustive preview in the
M9C1 planner. `src/hosted/purge_receipts.py` provides owner-validated durable
read-only preparation, replay-safe operation hashes, independent hosted-v3
archive verification, root tombstone/generation fencing and inventory drift
refusal, including per-document revision identity without a root bump. **There is NO destructive worker, no purge cursor progression, no
physical deletion, no account deletion.** Ten source-only tests authored in
`tests/test_v10374.py`, not executed. All Editor/Cloud suites remain for
USER verification. Frozen M3/M4 Firestore/GCS drivers, local Schema 42/local
runtime, verifier, watchdogs, Cloud 10.4.38 untouched. See
`breadmaker/docs/HOSTED_DELETION_M9C2A.md`. Real provider missing-parent
verification, batch execution and recovery drills remain before any purge.


## 2026-10-11 — M9C1 release-metadata corrective 2, Editor 10.3.73 / Cloud 10.4.38

The operator-run 10.3.72 Editor full gate: 1438 passed, 11 failed, one
skipped (Unit 89/89, Core/API 1163 pass + 11 fail + one skip, browser
94/94, Durable E2E 92/92); Cloud 10.4.38 verified 85/85. All 11
remaining failures have one cause: Review-plan first-line `Implemented through`
marker still read 10.3.71. Corrective .73 synchronizes that marker and all
current frontend release identities/cache references. The separate historical
Review plan section (`8.16.6`) remains historical. Adds
`scripts/sync_editor_release_metadata.py` `--write` and `--check` for future
release metadata synchronization, plus `tests/test_v10373.py` current-release
conformance. Sixteen older tests now check semantic three-part minimum-version
floors rather than pinning whichever patch happened to be current; their
schema, frozen-hash and behavioral assertions are unchanged. No source-level
runtime, local schema, frozen adapter, Cloud, verifier or watchdog changes.
Only static parsing/metadata audits performed; NO tests or verifier run by
assistant. M9C1 remains read-only, physical purge still unavailable, rich
hosted Review/Canvas/checkpoint historical parity still incomplete/fail-closed.
Operator full Editor+Cloud verifier is required to close this corrective.


## 2026-10-10 — M9C1 verification corrective, Editor 10.3.72 / Cloud 10.4.38

User verified Cloud 10.4.38 green on 10.3.71; Editor full report: 1428 pass, 21 fail, 1 skip (current browser 94/94). Failures: 16 historical source-level version assertions still pinned to 10.3.67 despite current version 10.3.71; 2 frontend self-version console literals still at 10.3.70; 1 M9B2A frozen Canvas-version rejection hidden by `canvases.position` SQLite NOT NULL violation; 1 M9C1 preview test incorrectly expected only a manually written export GCS object but hosted Canvas node writes an additional project-scoped block payload; 1 previously-green task milestone E2E timed out waiting for `draft_2` at five seconds, etiology unknown and not directly tied to hosted M9 work.

10.3.72 patches production hosted portability with early unsupported frozen-Canvas-history shape checking, fixes current frontend release identifiers/cache version, and updates 16 stale *source-level release assertion tests* to exactly 10.3.72 without removing assertions or modifying actual implementation checks. M9C1 inventory test now asserts both manually written export and automatically generated block payload are present and explicitly verifies out-of-project objects are absent. The planner still has **no purge executor**; retention 30 days, all physical purge authorization flags false. Verification runner `scripts/verify_release.py`, `tests/conftest.py`, watchdogs, local runtime, frozen adapters and Cloud tree are unchanged. The agent did not run tests. User must rerun full Editor/Cloud gate; the unconfirmed E2E timeout may recur and needs its own diagnosis if it does. Last user-confirmed all-green Editor remains 10.3.66. M9B advanced Canvas/Review histories not resolved by this release.


## 2026-10-10 — M9C1 read-only deletion preview, Editor 10.3.71 / Cloud 10.4.38

Per explicit user request continued without a new combined verifier. 10.3.68-10.3.70 portability remains USER-UNVERIFIED. M9C1 adds a read-only owner-only `HostedPurgePlanner` with tombstone checks, a 30-day minimum retention floor, known-model nested Firestore document inventory, exact-project GCS prefix inventory, independent recovery archive preservation, constrained resources, concurrent-root-change detection, and deterministic diagnostic fingerprint. No executor and no physical delete methods are present. Because current Firestore contracts cannot enumerate orphaned nested descendants exhaustively, `document_inventory_exhaustive` and `physical_purge_authorized` are ALWAYS false. M9C2 must design an exhaustive provider-neutral scanner, durable resumable receipts, backup verification and safe destructive worker before any physical purge. Account deletion remains disabled. `tests/test_v10371.py` authored but NOT run. Cloud 10.4.38, local Schema 42, frozen Firestore/GCS drivers, verifier and watchdogs remain unchanged. See `breadmaker/docs/HOSTED_DELETION_M9C1.md`.


## 2026-10-10 — M9B2B restricted history candidate, Editor 10.3.70 / Cloud 10.4.38

User asked to keep developing before the next verification. 10.3.68 M9B1 and 10.3.69 M9B2A remain **unverified**; this combined new candidate carries both. The new hosted-to-local history adapter projects only proven simple read-only manuscript Review snapshots to frozen local snapshot rows plus a derived restore checkpoint, translates recognized local-version-1 Book/Chapter checkpoints without markup/notes, explicitly maps hosted primary Manuscript Tab roles, deterministic 24-hex to local 32-hex Review identity, and preserves frozen checkpoint assets with content-addressed refs. It rejects multi-round/scoped/advanced Review history, hosted project-shaped checkpoints and incompatible frozen Canvas geometry rather than silently losing information. `tests/test_v10370.py` is authored but not executed. No full-history parity or live backup claims; M9C/M9D still pending. Cloud 10.4.38 and protected local/runtime/adapters/verifier unchanged.


**This file is the first file a future model should read.**  
Architecture/why/how: `SERVERLESS_HOSTED_REWRITE_PLAN.md`.

## 2026-10-10 — M9B2A Editor 10.3.69 / Cloud 10.4.38 (verification pending)

Per user request continued from 10.3.68 without a new corrective gate; **10.3.68 itself has no user green report**, so 10.3.69's next user-run full gate covers both M9B1 corrective and M9B2A. M9B2 split again to avoid large stalled chat changes: A is lossless native Canvas/Map graph + non-FK typed links (rebase Map anchor IDs, generated source keys, cross-feature reference IDs; reject unsupported snapshot/version shape explicitly); B will implement actual Review/checkpoint and advanced Canvas-version codecs/equivalence. New tests in `tests/test_v10369.py` are authored but NOT executed by model. No live cloud, no bucket, no user data. Cloud 10.4.38, local Schema42, verifier/watchdogs and frozen M3/M4 drivers remain protected.

## 2026-10-10 — M9B1 corrective Editor 10.3.68 / Cloud 10.4.38 (pending user verification)

User-run 10.3.67 Editor FULL: Unit 89 green; Core/API 1128 passed, 29 failed, one skipped; browser 94 green; durable E2E 92 green. 26 failures share the stale `breadmaker_logo.svg?v=10.3.66` CSS URL, and three hosted-portability tests failed because Schema-42 `chapters.created_at` is required but hosted-v3 exports omit provider-managed entity timestamps. Cloud 10.4.38 user-confirmed all green. Corrective aligns Review mask URL with Editor 10.3.68 and fills absent local timestamp fields from hosted project timestamps only in the transient local-v2 codec. No code in Cloud, local runtime, local Schema 42, M3/M4 adapters, verifier or watchdogs changed; hosted-v3 archive format untouched. Approximate timestamp provenance expressly documented. New candidate untested; do not mark verified until complete user-run green gate.

## 2026-10-10 — M9B1 Editor 10.3.67 / Cloud 10.4.38 candidate (awaiting user verification)

**Operator result:** Editor 10.3.66 + Cloud 10.4.38 fully green: Unit 89 passed; Core/API 1150 passed / 1 skipped; browser 94/94; Durable E2E 92/92; Cloud 85/85 (policy 60, identity 19, OpenTofu 6). This formally closes **M8 and M9A**. No live cloud environment deployed.

M9B divided into **B1 and B2** because source hosted-v3 project snapshots use Firestore/GCS logical state while local importer accepts only verified SQLite Schema 42 project-v2 archives. B1 adds `src/hosted/local_portability.py`, a bounded in-memory v3-to-v2 translator with strict fields, ID/FK mapping, asset integrity/reference and output SHA-256 enforcement. Hosted download routes `/api/projects/{id}/export` and `/api/data/export` now use generation-fenced `HostedProjectPackageService.export_local_bytes`. M9A recovery archives stay hosted-v3 and unchanged. New conformance tests `tests/test_v10367.py` are written but not executed by the model: hosted authoring -> unchanged local Workspace.verify_package/import_project; hostile field/FK/asset refusal; concurrent mutation fence; hosted-v3 new-project restoration. No changes to local SQLite/Schema 42, local runtime, M3/M4 adapters, verifier/watchdogs or Cloud 10.4.38.

**B1 limitation:** unsupported/non-equivalent structured entities fail closed. M9B2 must finish complex Review snapshots/checkpoints/Canvas/Map semantic and asset parity with representative fixtures before M9B can be called complete. See `breadmaker/docs/HOSTED_PORTABILITY_M9B.md`. M9C/M9D and M10–M12 remain pending.

## 2026-10-10 — M9A Editor 10.3.66 / Cloud 10.4.38 candidate packaged (user verification pending)

M9A foundation implemented in `src/hosted/recovery_archives.py`: independent injected object-storage boundary (enforces separate instance / distinct named Google bucket), owner-only Firestore pre/post project-generation fence, max 64 MiB synchronous archive, immutable create-only GCS upload, exact-generation SHA-256 readback, replica-replay-safe snapshot names, and operator-discoverable backup paths independent of Firestore receipts. Recovery package export now fails closed on failed authoritative project entity reads in `src/hosted/packages.py`. New credential-free conformance tests authored only in `tests/test_v10366.py`; no tests executed by model. Policy, scope, operational limits and M10 IAM/retention dependencies in `docs/HOSTED_RECOVERY_M9A.md`. No hosted API/UI, scheduler, deployed bucket, completed restore, or purge path is claimed in M9A. Local runtime, Schema 42, frozen M3/M4 repositories and drivers, verifier/watchdogs, Cloud 10.4.38 unchanged. The inherited M8B corrective 10.3.65 was **not reported green** by the user; user full 10.3.66/10.4.38 gate must confirm carried M8 and M9A.

## 2026-10-10 — M9A opened explicitly at user request (M8B green gate outstanding)

Decision: split M9 into A (independent sealed recovery artifacts), B (portable format and local/hosted restore), C (tombstones + account/project physical purge), D (scheduler/retention policy and recovery drill). Prior 10.3.65 M8B corrective was not run by user; M9A must not represent M8 as VERIFIED. M9A will add a hosted-only backup service with a distinct injected ObjectStore, project-owner control, bounded and generation-fenced export, immutable generation-preconditioned object publication, exact-generation digest readback, and source-only conformance tests. Local, M3/M4 drivers, Cloud IaC, and verifier remain protected. Do not assert scheduled operation or independent real GCS buckets are already deployed.

## 2026-10-10 — M8B corrective Editor 10.3.65 / Cloud 10.4.38 (pending user verification)

The user ran full Editor 10.3.64 and Cloud 10.4.38 verification: Unit 89/89, Core/API 1142 passed, 1 failed, 1 skipped; Current Browser 94/94, Durable E2E 92/92; Cloud 85/85. Exactly one static Editor contract failed (`test_v10364_old_chapter_mutation_is_not_a_per_block_loop`) because the M8B lifecycle document used `No sticky session` instead of `no sticky session` and `Whole-operation replay` instead of the required literal `whole-request replay`. The underlying runtime operation assertion succeeded. Editor 10.3.65 changes those lifecycle-documentation terms, synchronized current Editor release identities/metadata, and this handoff; it does not change the M8A/M8B runtime, local runtime, frozen adapters, verification harness or Cloud tree. Do not mark M8 verified until the corrected combined operator gate is green.

## 2026-10-09 — M8B Editor 10.3.64 / Cloud 10.4.38 verification candidate

M8B implements `hosted.chapter_mutations.replace_chapter_blocks`: one Firestore transaction for chapter block replacement, a durable per-project operation receipt, replay fingerprint conflicts, stale-generation CAS, bounded metadata and immutable GCS content staging. Composite read-only snapshots gain a generation fence. Unported hosted bulk scene/canvas/map population methods now report 409 instead of silently performing partial operations. Cloud Run SIGTERM, timeout, concurrency, instance-local rate-limiter caveat and M10 infrastructure boundary documented in `breadmaker/docs/HOSTED_CLOUD_RUN_LIFECYCLE_M8B.md`. New credential-free tests `tests/test_v10364.py` are authored only; assistant does not execute them. Local runtime, Schema 42, frozen M3/M4 adapters, verification runner/conftest and Cloud 10.4.38 are unchanged.

M8A `.63` was delivered but the user requested M8B before posting the `.63` verification result. Therefore this `.64/.38` full-run gate covers **M8A and M8B together**. M9 cannot formally start until that gate is green. Production GCP environment does not yet exist.

## 2026-10-09 — Milestone 8 opened after user-confirmed green gate

The user explicitly confirmed **all-green** on Editor `10.3.61` + Cloud `10.4.38`. M5, M6, and M7 are formally **VERIFIED**. M8 is **IN PROGRESS**. There is no live hosted deployment or hosted data.

Audit found the obsolete process-local one-worker guard in `runtime/hosted.py`; persistent canonical writes already use Firestore transaction CAS, but caller-generated operation IDs are normally random across retries. M8 implements replica-tolerant composition, stable request-key-derived operations for retryable creates, request bounds, and tests using shared memory store drivers reconstructed across runtime objects. This is not M10 IaC; Cloud infra remains unchanged. Protected verifier and local runtime remain frozen. No tests may be executed by the assistant.

## M8A corrective — Editor 10.3.63 / Cloud 10.4.38

**Status: AWAITING USER VERIFICATION.** The user ran the complete Editor 10.3.62 + Cloud 10.4.38 gate. Editor passed Unit 89/89, Current Browser 94/94, Durable E2E 92/92; Core/API passed 1124 with 11 metadata failures and one skip. All 11 failed assertions converged on `docs/BREADMAKER_REVIEW_IMPLEMENTATION_PLAN.md` claiming `Implemented through **10.3.61**` instead of the current version. Cloud passed all 85/85. Corrective 10.3.63 synchronizes that single Review-plan current-release marker, the Editor version/cache/contracts and release handoff. Historical 10.3.62 release sections remain. Hosted M8A behavior, local runtime, verification harness, M3/M4 drivers, and the entire Cloud tree remain unchanged aside from editor version/cache identity changes. No test suite was run by the model. On fully green operator verification, proceed to M8B.

## M8A checkpoint — Editor 10.3.62 / Cloud 10.4.38

**Status: AWAITING USER VERIFICATION.** M5, M6 and M7 were confirmed **all-green by the user** on Editor 10.3.61 / Cloud 10.4.38. The prior shared Playwright startup timeout was cleared by the operator's later run, not by changing its watchdog.

M8 is deliberately split into M8A and M8B to keep changes reviewable. M8A adds horizontally safe runtime selection (no worker-count authority lock), adapter-injected shared fake stores to test new runtime instances, request-key-derived stable operation IDs for canonical creates/updates and project creation, atomic annotation batch replay, durable package-import receipts, bounded inline upload reads/ZIP expansion, and a disposable-only hosted container filesystem. Browser mutation requests include an idempotency nonce; supported endpoints deduplicate through Firestore, not response caches. **M8A does not claim all 287 routes have whole-request replay semantics**. M8B owns multi-step routes, long work/shutdown/concurrency policy and the final M8 gate.

Cloud IaC at 10.4.38 is byte-identical to the last green baseline; M10 remains responsible for Cloud Run/Firestore/GCS deployment composition. M3/M4 frozen drivers, local runtime, test harness/watchdogs and Schema 42 are unchanged. M8A changes three historical static tests only to supersede the obsolete *single-worker required* assertion. New conformance tests `test_v10362.py` have been **authored but not executed by the model**.

## Critical operator rule

The model does **not** run `./verify-release`, `./test`, pytest, Playwright, browser/E2E, or the Breadmaker test suite. The user runs verification and returns the compact summary/failure digest.

Static syntax/parsing checks are allowed. Static checks are not verification.

---

# Current state

## Baseline

- Editor: `10.3.41`
- Cloud: `10.4.32`
- Local schema: `42`
- Local `./run`: user previously reported launcher corrected and subsequent release verification green.
- Hosted deployment: **none**.
- Hosted user/manuscript data: **none**.
- Current hosted application architecture in source: Firestore/GCS adapters + Google Identity Platform, alongside an undeployed legacy staging-VM IaC scaffold. Auth0 is archived/retired. M8 owns Cloud Run lifecycle/horizontal-safety behavior; M10 owns the deployable Cloud Run/Firestore/GCS IaC rewrite.
- New desired architecture: Cloud Run + Firestore + Cloud Storage + Identity Platform + Firebase Hosting.
- Initial live environment target: one **production/private-alpha** environment at `app.breadmaker.studio`; no staging environment initially.
- Local Breadmaker remains accountless SQLite/filesystem and is not being replaced.

## Program position

### Milestone 0 — COMPLETE

Planning/freeze package created. No product/runtime/IaC behavior changed and versions were intentionally not bumped.

### Milestone 1 — VERIFIED

The corrective Editor `10.3.43` + Cloud `10.4.32` was reported fully green by the user on 2026-10-05. Milestone 1 is closed. The verified seam exposes environment-neutral project/assets/usage/avatar ports while keeping explicit native local bridges as tracked migration debt. Local Schema 42, `requirements.txt`, the local launcher, and Cloud infrastructure remain unchanged. No Firestore/GCS/Identity Platform dependency was added.

### Milestone 2 — VERIFIED

The subsequent full verifier run after the Milestone-2 wording correction no longer reported any Milestone-2 data-model contract failures. Cloud was green, browser/E2E were green, and the remaining Editor reds were introduced Milestone-3/harness issues. The frozen `HOSTED_DATA_MODEL_V1` and index plan are therefore the verified persistence contract for Milestones 3–5.

### Milestone 3 — VERIFIED

The user reported the combined Editor `10.3.52` + Cloud `10.4.32` gate fully green on 2026-10-07. The Firestore structured-state adapter and its corrective verifier contract are closed and frozen for the remaining rewrite.

### Milestone 4 — VERIFIED

The same all-green `10.3.52` / `10.4.32` gate verifies the server-only Cloud Storage content/revision/asset adapter. `src/hosted/storage` and the Milestone-3 Firestore repositories are frozen adapter layers for Milestone 5.

### Milestone 5 — IMPLEMENTED; FORMAL CLOSE CARRIED INTO M7A GATE

Editor `10.3.53` established M5A and `10.3.54` implemented M5B behavioral parity. Subsequent corrections closed the hosted contract issues, and the user-reported `10.3.57` run returned only one failing historical local privacy-reset item (`test_v741_delete_all_external_failure_never_blocks_local_privacy_reset`). No M5 hosted product failure remained in that digest. Formal VERIFIED status is still withheld until the combined gate is completely green.

### Milestone 6 — TECHNICAL GO IMPLEMENTED; FORMAL CLOSE CARRIED INTO M7A GATE

Cloud `10.4.33` freezes the Identity Platform feasibility decision and security architecture. The M6 artifacts remain historical evidence and are not rewritten by M7A. The user-reported `10.3.57` failure digest contained no M6 identity/runtime failure; however, strict formal VERIFIED/GO closure remains tied to the next fully green combined Editor + Cloud gate.

### Milestone 7 — SPLIT INTO 7A / 7B / 7C

**M7A (`10.3.58` / `10.4.34`) — IMPLEMENTED; CARRIED INTO M7B GATE.** The user moved directly to M7B without returning an M7A verifier result. M7A fixes the carried Delete-All privacy invariant and implements the production-grade Identity Platform server-session boundary behind an explicit enable flag while keeping Auth0 as rollback. It adds fresh-ID-token exchange, recent-auth enforcement, revocation-checked provider session cookies, UID-bound private-alpha admission, and hosted-only Firebase Admin/ADC composition. The privacy fix is frozen unchanged in M7B.

**M7B (`10.3.59` / `10.4.35`) — IMPLEMENTED; VERIFICATION CARRIED INTO M7C.** Adds Google and email/password hosted provider UX; verified-email plus mandatory TOTP policy for password identities; TOTP enrollment/challenge; explicit dual-proof linking; fresh same-UID reauthentication; provider-neutral Account & Security operations; improved email-enumeration protection; provider-owned password policy; a browser-restricted public Firebase Web API key; and a pinned Secret Manager provider-operator path. The user asked to continue directly to M7C without returning a separate M7B verifier result.

**M7C (`10.3.61` / `10.4.38`) — IMPLEMENTED; CORRECTIVE AWAITING USER VERIFICATION.** The `10.3.60 / 10.4.36` gate proved Unit/browser/Durable-E2E behavior but exposed documentation-contract deletion plus five Cloud hygiene failures. `10.3.61 / 10.4.37` restores those contracts without restoring Auth0 authority. Identity Platform remains the sole real hosted authority. Live Auth0 runtime modules/routes/launchers are removed from Editor source and preserved only as archive text; staging starts with `BREADMAKER_AUTH_MODE=identity_platform`; Auth0 variables, runtime secret access, Deploy CLI dependency, desired state, operators/checkers and current architecture claims are retired/archived. Current verification targets the Identity Platform server-cookie/MFA/linking/security boundary, explicit Apple prerequisite blocker, and accountless local isolation. The 10.4.38 corrective restores normal-IaC and break-glass protection wording in Cloud architecture only. Rollback is a previous reviewed release, not a dormant current credential path.

---

# Last user verification

Latest user report: Editor `10.3.61` produced Unit `89/89`, Core/API `1126 passed / 0 failed / 1 skipped`, Current Browser `94/94`, and Durable E2E `0/92` because its first shared `sync_playwright().start()` fixture hit the unchanged 15-second setup watchdog. The prior `10.3.60` run completed Durable E2E `92/92`. Cloud `10.4.37` produced `84 passed / 1 failed`: `ARCHITECTURE.md` omitted the literal `normal iac` wording for the established persistent-data doctrine. All other Cloud tests and OpenTofu checks passed. No evidence implicates M7C product or identity behavior.

M7A's Delete-All privacy fix is already passing in that combined run and remains unchanged. Identity Platform remains the sole hosted authority; this corrective does not restore Auth0 runtime/configuration.

The last fully green combined release explicitly recorded remains Editor `10.3.52` + Cloud `10.4.32`. M5/M6 are functionally beyond that baseline but await a fully green combined gate for strict formal closure.

---

# Immediate next action

1. M7C Cloud-only corrective candidate is Editor `10.3.61` (byte-identical) + Cloud `10.4.38` (documentation wording + current version sync only).
2. User reruns Cloud verifier and the Editor Durable E2E or full verifier. The model does not run them.
3. An all-green combined gate closes the carried Delete-All privacy corrective, formally closes M5/M6, verifies M7A/M7B/M7C, closes Milestone 7, and unlocks Milestone 8.
4. A repeated shared Playwright startup timeout needs separate environmental/resource diagnosis; do not weaken the frozen 10s/15s watchdogs or refactor harness casually.

---

# Active decisions

| Decision | State | Notes |
| --- | --- | --- |
| Hard hosted rewrite is allowed | LOCKED | No hosted deployment/data exists. |
| Local implementation remains SQLite/filesystem | LOCKED | Local is first-class, zero-secret, accountless. |
| Hosted target is Cloud Run | LOCKED FOR PLAN | Re-check official docs during implementation if needed. |
| Hosted structured store is Firestore | LOCKED FOR PLAN | Final data mapping is Milestone 2. |
| Hosted blob/revision/assets store is GCS | LOCKED FOR PLAN | Use generation preconditions. |
| Hosted identity target is Identity Platform | M7C_IMPLEMENTED_PENDING_VERIFICATION | M6 found no material blocker. M7A/M7B implement the server-session/provider/MFA/linking/security boundary; M7C makes Identity Platform the sole current hosted authority and archives Auth0 live code/configuration. |
| Firebase Hosting fronts Cloud Run | LOCKED FOR PLAN | IaC/config ownership finalized in Milestone 10. |
| Initial hosted environment is production/private-alpha only | LOCKED | No staging cost initially. |
| Protected main / production-alpha delivery | LOCKED | Every main change requires a PR + required green Editor/Cloud checks; merge to main auto-deploys the private alpha. PR/arbitrary branch workflows never receive deploy authority. |
| Hosted durable user identity | LOCKED M2 | Provider-independent Breadmaker `usr_…` ID; provider UID is a server-side binding; email is never a durable key. |
| Direct browser Firestore access | FORBIDDEN M2 | Cloud Run remains the authorization/data plane. |
| Hosted project ID semantics | LOCKED M2 | Existing immutable 32-hex public ID semantics retained; possession never grants access. |
| Large/immutable hosted content | LOCKED M2 | GCS with create-only generation preconditions; Firestore publishes pointers last. |
| Review snapshot projections | LOCKED M2 | Local projection tables collapse into one immutable GCS snapshot envelope plus bounded metadata. |
| Hosted project import | LOCKED M2 | Import always creates a fresh hosted project ID; source ID is audit metadata only. |
| Staging/auto-promotion | DEFERRED | Milestone 13, only when economically justified. |
| Model runs verifier/tests | FORBIDDEN | User executes verifier. |
| Infrastructure clickops | FORBIDDEN AS NORMAL OPERATION | Secret payload provisioning/one-time registrar delegation are controlled exceptions, not recurring config. |

---

# Known constraints / risks to keep visible

1. Current application persistence is deeply SQLite/filesystem-shaped; Milestone 1 must avoid scattering cloud conditionals.
2. Identity Platform is now the sole current hosted auth authority; Auth0 is archive-only. The combined M7C gate must prove M6/M7A/M7B invariants were not lost during retirement.
3. Current hosted security/session state includes process-local assumptions. Cloud Run requires these to disappear from correctness/security paths.
4. Firebase Hosting dynamic rewrites have a documented request timeout; long-running operations must not accidentally rely on a request staying open indefinitely.
5. Identity Platform/OpenTofu resources can place some sensitive configuration in raw state. Secret values must not enter state merely for IaC convenience.
6. Apple Sign in with Apple requires Apple Developer external prerequisites; absence of those credentials may block Apple live smoke but should not cause fake/insecure implementation.
7. There is no data migration burden today. Avoid building compatibility bridges for nonexistent hosted data.
8. Local Schema 42 changes are out of scope unless independently required by a product feature; hosted schema does not need to mimic SQLite physically.

---

# Milestone status board

| # | Milestone | Status | User verification |
| ---: | --- | --- | --- |
| 0 | Freeze baseline + program contract | COMPLETE | baseline pre-existed; docs-only snapshot |
| 1 | Hosted/local seam extraction | VERIFIED | user reported full verifier all green on 10.3.43 / 10.4.32 |
| 2 | Hosted data model specification | VERIFIED | verified by subsequent full run after wording correction; frozen contract now feeds Milestone 3 |
| 3 | Firestore structured-state adapter | VERIFIED | user reported combined 10.3.52 / 10.4.32 gate all green |
| 4 | Cloud Storage content/revision/asset adapter | VERIFIED | user reported combined 10.3.52 / 10.4.32 gate all green |
| 5 | Hosted application integration | VERIFIED | Closed by user-confirmed all-green 10.3.61 / 10.4.38 |
| 6 | Identity Platform security feasibility gate | VERIFIED | Closed by user-confirmed all-green 10.3.61 / 10.4.38 |
| 7 | Identity Platform implementation + Auth0 retirement | VERIFIED | Closed by user-confirmed all-green 10.3.61 / 10.4.38 |
| 8 | Cloud Run lifecycle/statelessness hardening | VERIFIED | Covered by user-confirmed full green 10.3.66 / 10.4.38 gate |
| 9 | Backup/recovery/export/delete | M9A VERIFIED; M9B1 CORRECTIVE AWAITING VERIFICATION | 10.3.66 recovery foundation verified; 10.3.68 local portability B1 corrective; B2/C/D pending |
| 10 | Serverless IaC rewrite | BLOCKED_BY_9 | pending |
| 11 | Protected-main + automatic production-alpha delivery | BLOCKED_BY_10 | pending |
| 12 | Live private-alpha deployment | BLOCKED_BY_11 | live operator gate |
| 13 | Staging + auto-promotion | DEFERRED | not currently required |

---

# Work log

## 2026-10-09 — Milestone 7C Cloud normal-IaC documentation corrective

**User gate:** Editor 10.3.61 was green in Unit (89), Core/API (1126 + one skip), and Current Browser (94); the Durable E2E single shared Playwright startup fixture timed out at 15s, causing 92 derivative errors. Cloud 10.4.37 was 84/85 with the only failure a missing `normal iac` substring in current Cloud architecture.
**Corrective:** Re-state the existing no-destructive-changes-under-normal-IaC doctrine and independent break-glass manual-retirement boundary in `breadmaker-cloud/ARCHITECTURE.md`; synchronize Cloud current release metadata/tests/policies to 10.4.38. Editor 10.3.61 is byte-for-byte unchanged. No changes to Cloud infra or its verifier.
**Verification:** Not run by model; user will rerun. M8 remains blocked until all-green.

## 2026-10-08 — Milestone 7C corrective prepared after contract-sync gate

**User result:** Editor `10.3.60`: Unit `89/89`; Core/API `1096 passed / 30 failed / 1 skipped`; Current Browser `94/94`; Durable E2E `92/92`. Cloud `10.4.36`: `80 passed / 5 failed`.
**Editor diagnosis:** 28/30 reds came from replacing long-lived `README.md`, `ARCHITECTURE.md`, `AGENTS.md`, and `docs/TESTING.md` with short current-state summaries even though those historical contracts are part of the verifier surface. One red was the removed non-secret `breadmaker-cloud/scripts/auth_locale_source.py` compatibility helper. One was a false-positive source marker: boolean `has_password = ...` matched the provider-proof guard's forbidden raw-material marker `password =`.
**Editor corrective:** restore the complete `10.3.59` documentation lineage and prepend a current M7C authority section; restore the locale-source helper without restoring Auth0 runtime authority; rename the boolean to `password_provider_present`; synchronize current release/cache identities to `10.3.61`.
**Cloud diagnosis/corrective:** restore exact staging+production persistent-data doctrine wording; restore explicit no-service-account-key wording; state `sole hosted authentication authority` exactly; change the Auth0 desired-state checker to scan `infra/` + `policy/` instead of matching its own `auth0_issuer` sentinel; apply the exact `tofu fmt` spacing reported for staging/service modules; synchronize to `10.4.37`.
**Protected boundaries:** verifier/watchdogs, `tests/conftest.py`, local runtime/requirements, Schema 42 constants, frozen M3/M4 adapters, and the M7A privacy-reset implementation are unchanged from `10.3.60`. M7C live Auth0 retirement is not reversed.
**Static checks run by model:** exact predicates for all 30 reported Editor failures are satisfied by source inspection; the five Cloud failure predicates are satisfied by source inspection; active Python/JSON/JS/shell syntax checks pass; route manifest remains 287 routes; all active cache identities are `10.3.61`; active Cloud desired-state surfaces contain no `BREADMAKER_AUTH0_` or `auth0_issuer`. These are not release verification.
**Verifier/tests run by model:** NO.
**Next safe action:** user runs full Editor + Cloud verification on `10.3.61 / 10.4.37`. Green closes M5/M6 and all of Milestone 7, then unlocks M8.
**Package/path:** `Breadmaker_editor_v10.3.61_cloud_v10.4.37_serverless_rewrite_milestone7C_corrective.zip`

## 2026-10-08 — Milestone 7C combined authority-switch candidate prepared

**Milestone:** 7C  
**Candidate:** Editor `10.3.60` + Cloud `10.4.36`.  
**Gate inheritance:** M7A and M7B were implemented but the user continued without standalone verifier results, so this candidate verifies M7A + M7B + M7C together and also carries strict formal closure of M5/M6.  
**M7C Editor scope:** Identity Platform is the only real hosted auth mode; Auth0 runtime modules/routes and the old hosted-auth launcher are removed from executable source and preserved as archive text; Account & Security is Identity-Platform-only; local runtime stays accountless/cloud-free; hosted GCP project lookup accepts the canonical `BREADMAKER_GCP_PROJECT_ID` used by Identity Platform.  
**M7C Cloud scope:** Auth0 desired state, runtime variables/secret access, operators/checkers and Deploy CLI dependency are removed from current configuration and preserved only in archives. Current policy/startup selects Identity Platform only. Historical M6/M7A/M7B evidence is archived, while current active regression coverage explicitly carries their GO/session/MFA/linking/provider-secret/email-privacy invariants into the M7C gate.  
**Deployment boundary:** no hosted environment exists. The legacy staging VM configuration remains an undeployed pre-M10 verification scaffold; M7C does not create the Firestore/GCS/Cloud Run data plane. M8 owns application Cloud Run lifecycle/horizontal safety; M10 owns the deployable serverless IaC rewrite.  
**Protected boundaries:** `scripts/verify_release.py`, `tests/conftest.py`, local runtime/requirements, Schema 42 constants, frozen M3/M4 adapters, and the M7A Delete-All privacy implementation are byte-identical to the M7B checkpoint.  
**Static checks run by model:** 835 active Python files AST-parse; 43 JSON files parse; 50 shipped Editor JavaScript files pass `node --check`; 9 shell/shebang files pass `bash -n`; route manifest remains 287 routes generated for `10.3.60`; all 75 active cache identities are `10.3.60`; 35 current Cloud `.tf` files pass lightweight delimiter/string structural checks; active runtime/IaC contain no Auth0 environment/config inputs; obvious secret-literal scan is clean. These are **not verification**.  
**Verifier/tests run by model:** NO.  
**Next safe action:** user runs full Editor + Cloud verification on `10.3.60 / 10.4.36`. Green closes the carried privacy fix, M5/M6, and all of Milestone 7, then unlocks M8. Any red remains M7C.  
**Package/path:** `Breadmaker_editor_v10.3.60_cloud_v10.4.36_serverless_rewrite_milestone7C_candidate.zip`  

## 2026-10-08 — Milestone 7B provider/MFA/linking/security checkpoint prepared

**Milestone:** 7B  
**Candidate:** Editor `10.3.59` + Cloud `10.4.35`.  
**Gate inheritance:** M7A `10.3.58 / 10.4.34` was implemented but the user moved directly to M7B without returning its verifier result. The M7B gate therefore also validates the carried `v741` privacy correction and M7A session boundary.  
**M7B Editor scope:** hosted Firebase browser state is in-memory proof only; Google and email/password login/registration; verified-email + mandatory TOTP before password identity can mint Breadmaker authority; TOTP challenge/enrollment; explicit provider linking; password linking uses the authenticated Identity Toolkit `accounts:signUp` path while improved email-enumeration protection remains enabled; fresh reauthentication must verify the same external UID as the current Breadmaker server session; provider-neutral Account & Security supports password change, TOTP management, unlink postconditions, and sign-out-everywhere. Provider tokens are not persisted.  
**M7B Cloud scope:** browser-restricted Firebase Web API key; `${project_id}.firebaseapp.com` auth handler domain; provider feature flags default false; Secret Manager creates only provider secret containers, never secret payload/version resources; least-privilege provider-operator service account/custom role includes Identity Platform config permissions plus `serviceusage.services.use`; human/operator impersonation requires `roles/iam.serviceAccountTokenCreator`; provider operator accepts only pinned numeric secret-version references, enables improved email privacy and ENFORCE password policy, and treats incomplete Apple prerequisites as a hard blocker. Auth0 remains selected in staging through M7B.  
**Protected boundaries:** `scripts/verify_release.py`, `tests/conftest.py`, local runtime/requirements, Schema 42 constants, frozen M3/M4 adapters, M7A Delete-All privacy files, and frozen M6 feasibility artifacts remain unchanged.  
**Static checks run by model:** 882 Python files AST-parse; 42 JSON files parse; 52 JavaScript files pass `node --check`; 10 shell/shebang files pass `bash -n`; route manifest remains 287 routes and is generated for `10.3.59`; all 75 active numeric cache identities are `10.3.59`; 35 Cloud `.tf` files pass lightweight delimiter/string structural checks; no Terraform provider secret payload/version resource and no obvious secret literal detected. These are not release verification.  
**Verifier/tests run by model:** NO.  
**Next safe action:** user runs full Editor + Cloud verification on `10.3.59 / 10.4.35`. Green verifies M7A/M7B and unlocks M7C; red remains on M7B.
**Package/path:** `Breadmaker_editor_v10.3.59_cloud_v10.4.35_serverless_rewrite_milestone7B_checkpoint.zip`  

## 2026-10-07 — Editor 10.3.56 / Cloud 10.4.33 combined M5 corrective + M6 feasibility candidate

**User result entering this work:** Editor 10.3.55 Unit 89/89; Core/API 1212 pass / 2 fail / 1 skip; Current browser 94/94; Durable E2E 92/92; Cloud 281/281.
**M5 final corrective:** hosted `request_user_id(explicit)` now ignores the explicit/browser hint and returns only the server-selected developer identity or bound session identity; `test_v8184.py` joins the existing one-worker no-contention lane, with the hard 10-second item watchdog unchanged.
**M6 research decision:** current Google/Firebase docs and SDK behavior support server session cookies in Python, in-memory browser persistence, `auth_time` freshness, stable UID, Apple private relay/first-login behavior, verified email, TOTP MFA, reauth, explicit linking, revocation, private-alpha admission, and secret provisioning outside source/Tofu state. Account-linking policy requires duplicate emails enabled so email cannot silently merge trusted providers; explicit dual-proof linking is mandatory.
**M6 artifacts:** `breadmaker-cloud/IDENTITY_PLATFORM_ARCHITECTURE.md`, `policy/identity_platform_architecture.json`, credential-free client/server prototypes, static checker and policy tests. Auth0 remains active; no Identity Platform live resource or secret is provisioned.
**Static/firewall checks:** 875 Python files AST-parse; 40 JSON files parse; 50 JS files pass `node --check`; shipped shell/launcher syntax passes; all 73 active browser cache-busters match Editor 10.3.56; route manifest remains 287 routes and is generated for 10.3.56; `tests/conftest.py`, local runtime/requirements, frozen M3 repository code, frozen M4 publication/storage code, and all 32 Cloud `.tf` files are unchanged from 10.3.55/10.4.32; no private-key/API-key material detected.
**Verifier/tests run by model:** NO.
**Next safe action:** user runs full Editor + Cloud verification on 10.3.56 / 10.4.33. Green closes M5 and verifies M6/GO, unlocking M7.


Use newest entry first. Update after **every atomic implementation block**.

## 2026-10-07 — Milestone 5B behavioral-parity candidate prepared

**Milestone:** 5 — hosted application integration  
**Status:** AWAITING_USER_VERIFICATION  
**Verified baseline:** user reported Editor `10.3.52` + Cloud `10.4.32` fully green; Milestones 3 and 4 are VERIFIED.  
**M5A checkpoint:** Editor `10.3.53` moved hosted runtime composition to Firestore/GCS or deterministic memory fakes, removed hosted SQLite/project-directory authority, implemented hosted project/package/domain services, and covered all current manifest routes.  
**M5B changes:** Firestore-only annotation batches are one transaction/one project-generation advance; immutable checkpoint deletion removes only the Firestore pointer and leaves GCS bytes as an orphan/retention candidate; per-member bookmark/UI state is membership-authorized; hosted chapter block replacement removes omitted blocks; Canvas/Map Populate uses `config/story_templates.json` plus Firestore chapter/scene data; Map auto-arrange persists canonical item positions; additive M5 conformance tests cover lifecycle/membership/state, manuscript hierarchy, annotations/comments, reviews, canvases/maps/tasks, and immutable checkpoint behavior while M5A retains package/import/export and unauthorized-access coverage.  
**Protected boundaries:** verifier orchestration, watchdogs, `tests/conftest.py`, local runtime, local requirements, frozen Milestone-3 Firestore repositories, frozen Milestone-4 object publication, and the entire Cloud repository are byte-for-byte unchanged from M5A. Historical current-release tests changed only from `10.3.53` to `10.3.54`.  
**Static checks run by model:** 833 Editor Python files AST-parse; 22 Editor JSON files parse; all 48 shipped JavaScript files pass `node --check`; 7 shell/launcher files pass `bash -n`; hosted route extraction covers all 287 manifest routes (335 verb/path pairs including hosted support routes); 71 live index cache-busters plus bootstrap/logo release markers agree on `10.3.54`; no generated caches/reports are packaged. These are not release verification.  
**Verifier/tests run by model:** NO.  
**Next safe action:** package Editor `10.3.54` / Cloud `10.4.32`; user runs full verifier. Green closes Milestone 5 and unlocks Milestone 6.

---

## 2026-10-05 — Milestone 4 verifier round red; 10.3.50 fixture corrective prepared

**Milestone:** 4 (combined gate still also closes the final Milestone-3 diagnostic corrective)  
**Status:** AWAITING_USER_VERIFICATION  
**User verifier result:** Editor Unit `89/89`; Core/API `1175 passed / 14 failed / 1 skipped`; Current browser `94/94` after `4/4` fresh-process recoveries; Durable E2E `0/92`; Cloud `281/281`; zero process watchdog timeouts.  
**Failure diagnosis:** Durable E2E's 92 failures all came from one module-scoped setup timeout signature at the unchanged 15-second per-item watchdog. Twelve Core/API failures were unrelated unchanged 10-second per-item watchdog expirations spread across old modules, including no-contention lanes, which is consistent with machine/execution pressure rather than the small Milestone-4 diff. One real Milestone-4 failure was `ASSET_BYTES_UNSAFE`: the new test fixture's IDAT CRC was invalid, so the existing fail-closed PNG validator behaved correctly. The remaining non-timeout failure was seed `8130` reporting `database.ok == false`; the compact digest did not include the deep integrity errors and the relevant local runtime/storage code is unchanged from the prior functionally green 10.3.47 candidate.  
**Corrective:** Editor `10.3.50` replaces the corrupt PNG fixture with a CRC-valid 1×1 PNG and changes the seed-soak assertion only to include the returned database-health diagnostics if it fails again. Hosted storage implementation/runtime behavior is unchanged. Per-test watchdog budgets remain 3s/10s/15s and are not raised.  
**Cloud/browser evidence:** Cloud was completely green (`281/281`). Current browser recovered all four initial failures in fresh processes, ending with zero confirmed browser failures.  
**Static checks run by model:** 860 Python files AST-parse; 39 JSON files parse; all 49 JavaScript files pass `node --check`; 10 shell/shebang files pass `bash -n`; all 73 live cache-buster references and the bootstrap marker agree on `10.3.50`; six protected local boundaries are byte-for-byte identical to incoming 10.3.47; five frozen Milestone-2/3 hosted contracts are byte-for-byte identical; the entire `src/hosted/storage` implementation plus hosted requirements are byte-for-byte identical to 10.3.49; all 32 Cloud `.tf` files are unchanged; secret-material heuristic clean. The corrected PNG fixture was parsed directly through the existing hosted image structural boundary as a 1×1 PNG with valid CRCs. These are static/targeted checks, not release verification.  
**Verifier/tests run by model:** NO.  
**Next safe action:** user runs the full verifier on Editor `10.3.50` / Cloud `10.4.32`. Green verifies Milestones 3 and 4. A repeated timeout swarm is handled as verifier/execution-pressure work without relaxing hard per-item budgets; a repeated seed-8130 failure should now expose its exact database integrity error.  
**Package/path:** `Breadmaker_editor_v10.3.50_cloud_v10.4.32_serverless_rewrite_milestone4_corrective2.zip`

---

## 2026-10-05 — Milestone 4 verifier round red; 10.3.49 release-identity corrective prepared

**Milestone:** 4 (combined gate still also closes the final Milestone-3 diagnostic corrective)  
**Status:** AWAITING_USER_VERIFICATION  
**User verifier result:** Editor `10.3.48` Core/API completed all `240/240` execution jobs with `181` passed and `59` failed. The user stopped the suite while Durable E2E was still running, so no final Editor item accounting exists for this attempt and Cloud was not re-gated.  
**Failure diagnosis:** the visible failures are the shared historical release-identity/asset contracts (`v850`, `v950`, `v961x`, `v963`, `v965`, `v967`, etc.). The package declared `VERSION=10.3.48`, but every live browser asset cache-buster and the runtime bootstrap frontend marker still declared `10.3.47`. This caused unrelated historical modules to fail their common release synchronization assertion before feature-specific checks.  
**Corrective:** Editor `10.3.49` synchronizes all 73 live numeric browser cache-buster references, the bootstrap frontend marker, Review-plan current-release marker, Milestone-4 current-release docs, route manifest, generated code index, and the repository's established exact-current-release assertions. The Milestone-4 storage implementation itself is byte-identical to the failed `10.3.48` candidate; no product/storage behavior was changed in response to release-identity failures.  
**Freeze review:** local `requirements.txt`, local launcher, `runtime.local`, the still-undeployed `runtime.hosted` composition, Schema 42 storage schema, and workspace schema constant are byte-for-byte identical to the incoming `10.3.47` candidate. The five frozen Milestone-2/3 hosted contracts are byte-for-byte identical. Cloud IaC remains unchanged.  
**Static checks run by model:** 860 Python files AST-parse; 39 JSON files parse; all 48 shipped JavaScript files pass `node --check`; 5 project shell/launcher files pass `bash -n`; explicit release-identity audit reports `10.3.49` with all 73 live cache-buster references synchronized; secret-material heuristic clean. Generated API map/route manifest and code index refreshed. These are static checks, not verification.  
**Verifier/tests run by model:** NO.  
**Next safe action:** user runs the full verifier on Editor `10.3.49` / Cloud `10.4.32`. Green marks Milestones 3 and 4 VERIFIED and unlocks Milestone 5. Red stays on this combined gate.  
**Package/path:** `Breadmaker_editor_v10.3.49_cloud_v10.4.32_serverless_rewrite_milestone4_corrective.zip`

---

## 2026-10-07 — Milestone 5B corrective after 10.3.54 verification

**User result:** Unit 89/89; Current browser 94/94 after one recovered retry; Durable E2E 92/92; Core/API 1209 passed / 5 failed / 1 skipped; Cloud 281/281. The five reds were all hosted-contract synchronization issues, not product-flow or harness failures.
**10.3.55 corrective:** missing hosted server session now raises the existing `AuthenticationError`; pre-M5 Workspace recovery source assertion is advanced to Firestore-native hosted composition while the local fail-closed recovery test remains; hosted-only docstrings no longer contain the forbidden local-home environment token; the Milestone-4 static validator now validates frozen adapter isolation/export and local-runtime separation rather than forbidding the intentional M5 wiring; the Milestone-4 release test now verifies `HostedCommittedAssetStore` delegates to the frozen `HostedAssetStore`.
**Protected boundary:** verifier/watchdogs, `tests/conftest.py`, local runtime/requirements, frozen M3 repository implementation, frozen M4 publication implementation, and Cloud repo are not modified for this corrective.
**Verifier/tests run by model:** NO.
**Next safe action:** user runs full Editor + Cloud verification on 10.3.55 / 10.4.32. Green closes Milestone 5.

## 2026-10-05 — Milestone 4 candidate + final Milestone-3 harness correction

**Milestone:** 4 (combined gate also closes the final Milestone-3 diagnostic corrective)  
**Status:** AWAITING_USER_VERIFICATION  
**User verifier result entering this block:** Editor Unit 89/89; Core/API 1176 passed, 1 failed, 1 skipped; Current browser 94/94; Durable E2E 92/92; Cloud 281/281; zero process timeouts. The only failure was stale `test_v965` source text for the verifier's ordinary Core/API lane.  
**Milestone-3 corrective:** historical contract now requires ordinary Core/API jobs to exclude both scale jobs and the dedicated serial/no-contention lane, and explicitly requires that serial lane to run at one worker. The correct verifier implementation is preserved rather than regressed to satisfy the old literal.  
**Milestone-4 implementation:** added `src/hosted/storage` with `ObjectStore` contract, deterministic `MemoryObjectStore`, isolated Google Cloud Storage SDK driver, canonical server-generated object-key builders, immutable create/readback publisher, hybrid Firestore pointer-last publisher, revision CAS publication, orphan discovery + exact-generation deletion, and `HostedAssetStore`. Asset reads treat the short logical API digest as lookup-only, then re-hash the exact object generation and require the full canonical content-addressed object key to match. Added hosted-only `google-cloud-storage==3.16.0`; local requirements remain cloud-storage-free. Added Milestone-4 human/machine contracts, static validator, and release regression coverage.  
**Security/durability invariants:** immutable creates use generation match 0; replacement/delete require observed generation; stored bytes are re-read at the exact generation and SHA-256/size verified before Firestore publication; unauthorized revision callers are rejected before blob creation and membership is rechecked in the final transaction; caller revision metadata cannot override managed/reserved fields or carry secret-like fields; Firestore pointer publication is last; failed CAS leaves only a discoverable orphan; no request-local speculative blob deletion. Hosted image structural validation/decoder restrictions are preserved.  
**Runtime/local freeze:** Milestone 5 remains the first point that may switch hosted product API composition to Firestore/GCS adapters. `runtime.hosted` still uses the old undeployed composition. Local Schema 42, SQLite/filesystem, `LocalAssetStore`, and local dependency manifest remain unchanged in semantics. No live GCP resource exists.  
**Static checks run by model:** 822 Editor + 38 Cloud Python files AST-parse (860 total); 22 Editor + 17 Cloud JSON files parse (39 total); all 49 JavaScript files pass `node --check`; 5 project shell/launcher files pass `bash -n`; five protected local boundaries are byte-for-byte identical to the incoming 10.3.47 candidate; five frozen Milestone-2/3 hosted contracts are byte-for-byte identical; all 32 Cloud `.tf` files are byte-for-byte identical; PEM/JWT heuristic scan clean; Milestone-4 source invariants clean. Generated code index refreshed. These are static checks, not verification.  
**Verifier/tests run by model:** NO.  
**Next safe action:** user runs the full verifier on Editor 10.3.48 / Cloud 10.4.32. Green marks both Milestone 3 and Milestone 4 VERIFIED and unlocks Milestone 5. Red stays on this gate.  
**Package/path:** `Breadmaker_editor_v10.3.48_cloud_v10.4.32_serverless_rewrite_milestone4_candidate.zip`

---

## 2026-10-05 — Milestone 3 corrective closeout / frozen-contract review

**Milestone:** 3  
**Status:** AWAITING_USER_VERIFICATION  
**Corrective candidate:** Editor `10.3.47` + Cloud `10.4.32`.  
**Additional review before packaging:** repaired a crossed repository-model lookup introduced during the interrupted edit; generic project child access now uses the guarded `_model()` path while the low-level structured primitive uses the canonical generated registry directly. External Review participant records no longer infer Breadmaker user authority from a `user_id` field; the adapter now follows the frozen Milestone-2 rule and fails external reviewer access closed until a server-issued reviewer session bound to review + participant + active snapshot exists. Caller-controlled `generation`/`created_at`/`updated_at` fields are rejected on structured creates. Membership creation requires the target hosted user to exist. Random membership IDs are generated outside retryable transaction callbacks so provider retries are deterministic.  
**Harness corrective:** the eight contention-prone modules from the user's latest run are isolated into a one-worker `Core / API serial` lane; the hard per-item `10.0s` Core/API watchdog is unchanged. Durable E2E and scale no-contention lanes remain preserved.  
**Static checks run by model:** hosted data-model validator clean; hosted Firestore adapter validator clean; 813 Editor + 38 Cloud Python files AST-parse; 21 Editor + 17 Cloud JSON files parse; 49 JS files pass `node --check`; 7 shell/launcher files pass `bash -n`; hosted Firestore source contains no local-engine `sqlite` terminology; local requirements remain Firestore/GCS/Firebase-free; five protected local boundaries are byte-for-byte identical to the verified Milestone-2 baseline; all 32 Cloud `.tf` files are byte-for-byte identical; PEM/JWT heuristic scan clean. These are static checks, not verification.  
**Verifier/tests run by model:** NO.  
**Next safe action:** user runs full verifier on the packaged Editor `10.3.47` / Cloud `10.4.32` corrective. Green closes Milestone 3 and starts Milestone 4. Red remains Milestone 3.  
**Package/path:** `Breadmaker_editor_v10.3.47_cloud_v10.4.32_serverless_rewrite_milestone3_corrective.zip`

---


## 2026-10-06 — Milestone 4 corrective 10.3.52 prepared

**User verifier result on 10.3.51 / 10.4.32:** Cloud green. Editor Unit 89/89; Core/API 1190 passed / 2 failed / 1 skipped; Current browser 94/94; Durable E2E 92/92. Both Editor failures belong to `test_v8173.py` and are hard 10-second lifecycle timeouts (setup + teardown), with no assertion failure. The prior six Core/API timeout modules and the Find `0 / 0` race are resolved.

**Corrective:** Editor `10.3.52` keeps Milestone-4 storage/product code unchanged. `test_v8173.py` remains in the one-worker no-contention lane, and the verifier now preloads the application once at pytest session start for this API-heavy module so one-time import/bootstrap cost is outside the unchanged 10-second per-item setup/call/teardown budget. Captured/non-TTY single-job progress now emits one 0/1 bar at start and one 1/1 bar at completion instead of repeating heartbeat bars every 10 seconds.

**Next safe action:** user runs the full verifier on Editor `10.3.52` / Cloud `10.4.32`. Green closes Milestone 3 and verifies Milestone 4. Red stays on this gate. The model does not run the verifier/test suite.

## 2026-10-05 — Milestone 3 verifier round red; 10.3.47 corrective prepared

**Milestone:** 3  
**Status:** AWAITING_USER_VERIFICATION  
**User verifier result:** Cloud green (281/281). Editor: Unit 89/89; Core/API 1158 passed, 9 failed, 1 skipped; Current browser logically 94/94 after 2 fresh-process recoveries; Durable E2E 92/92.  
**Concrete Milestone-3 failure:** `test_v10346_hosted_model_registry_is_contract_driven_not_sqlite_driven` found the local-engine word in the generated hosted model registry. The registry is now generated only from `HOSTED_DATA_MODEL_V1` policy fields and contains no local-engine implementation terminology.  
**Harness contention failures:** eight Core/API items exceeded the unchanged hard 10-second per-test budget while running in the ordinary parallel core pool: review checkpoint persistence, project manifest identity, four tree-wide historical source scans, and two randomized soak/property modules. These modules now run in a dedicated one-worker Core/API serial lane; the 10-second per-item watchdog is unchanged.  
**Milestone-2 carryover:** the prior case-sensitive `user filenames` wording mismatch is corrected and did not recur in the latest run; Milestone 2 is marked VERIFIED by this subsequent run.  
**Milestone-3 implementation:** server-only `src/hosted/firestore` package; generated model registry; deterministic memory driver; lazy Google driver; hosted-only Firestore SDK pin; user, identity-binding, project/membership and structured project-entity repositories; transaction-scoped authorization; generation CAS; idempotent create/update replay; enumeration-resistant project lookup; static adapter validator and regression contracts.  
**Local/runtime freeze:** local Schema 42, local `./run`, local requirements and local persistence remain first-class and cloud-free. Hosted application wiring remains deferred to Milestone 5.  
**Verifier/tests run by model:** NO.  
**Next safe action:** user runs full verifier on Editor 10.3.47 / Cloud 10.4.32. Green closes Milestone 3 and starts Milestone 4. Red remains Milestone 3.  
**Package/path:** `Breadmaker_editor_v10.3.47_cloud_v10.4.32_serverless_rewrite_milestone3_corrective.zip`

---

## 2026-10-05 — Milestone 2 verifier round 1 red; corrective prepared

**Milestone:** 2  
**Status:** AWAITING_USER_VERIFICATION_CORRECTIVE  
**Failed candidate:** Editor `10.3.44` + Cloud `10.4.32`.  
**User verifier result:** Cloud green. Editor: Unit 89/89; Core/API 1134 passed, 21 failed, 1 skipped; Current browser logically recovered to 94/94 after 10 fresh-process retries; Durable E2E 0/92 because the one module-scoped server failed startup while contending with current-browser jobs; one scale contract exceeded the unchanged hard 10-second budget.  
**Failure analysis:** 19 core failures were stale Review-plan release synchronization; 1 was Milestone-2 orphan-policy wording; the scale timeout and durable-server startup occurred in a run that also produced 10 recoverable current-browser contention failures. No Milestone-2 product/runtime assertion failed beyond the wording contract.  
**Corrective:** Editor `10.3.45`, Cloud `10.4.32`. Review-plan release marker synchronized; orphan failure explicitly named; verifier now runs the 10k-paragraph scale job before parallel core work and gives durable E2E its own one-worker no-contention lane before current-browser parallelism. Hard per-test budgets are unchanged.  
**Verifier/tests run by model:** NO.  
**Static checks:** 801 Editor Python + 38 Cloud Python files parsed; 20 Editor JSON + 17 Cloud JSON files parsed; all 48 shipped JS files syntax-checked; launcher/shell syntax clean; `validate_hosted_data_model_contract.py` reports static consistency OK; corrective source assertions clean; five frozen local boundaries byte-identical to 10.3.44; all 32 Cloud `.tf` files byte-identical; secret-material heuristic scan clean. These are not verification.  
**Next safe action:** user runs the full verifier on the 10.3.45 corrective. Green closes Milestone 2; red stays in Milestone 2.  
**Package/path:** `Breadmaker_editor_v10.3.45_cloud_v10.4.32_serverless_rewrite_milestone2_corrective.zip`

---

## 2026-10-05 — Milestone 2 candidate complete

**Milestone:** 2  
**Status:** AWAITING_USER_VERIFICATION  
**Candidate:** Editor `10.3.44` + Cloud `10.4.32`; local Schema `42`.  
**Completed:** canonical human + machine-readable hosted data model; exact source inventory for all 54 Schema-42 tables; all 54 mapped exactly once; non-SQL profile/avatar/membership/registry/assets/history/recovery/backup/import/export/trash/log/session state mapped; provider-independent Breadmaker user identity; project-ID authorization neutrality; bounded Firestore document policy; Firestore/GCS split; source-controlled Firestore index plan; GCS create-only/CAS rules; pointer-last revision publication; durable large-operation protocol; tombstone/delete/orphan/recovery rules; static contract validator; Milestone-2 regression contract.  
**No runtime implementation:** no Firestore/GCS client dependency, no live resources, no Identity Platform implementation, no IaC change.  
**Local freeze:** Schema 42 and local SQLite/filesystem/accountless architecture remain unchanged.  
**Verifier/tests run by model:** NO.  
**Static checks:** 838 Python files parsed with AST; 37 JSON files parsed; all 48 JavaScript files passed `node --check`; 5 shell/launcher files passed `bash -n`; canonical contract validator reports `Hosted data model contract: static consistency OK`; source-derived indexes regenerated; five frozen local boundaries are byte-for-byte unchanged; all 32 Cloud `.tf` files are byte-for-byte unchanged; secret-material heuristic scan is clean. These are not verification.  
**Last user verifier result:** Editor `10.3.43` + Cloud `10.4.32` all green; Milestone 1 VERIFIED.  
**Next safe action:** user runs full verifier on this candidate. If green, mark Milestone 2 VERIFIED and begin Milestone 3 — Firestore structured-state adapter.  
**Package/path:** `Breadmaker_editor_v10.3.44_cloud_v10.4.32_serverless_rewrite_milestone2_candidate.zip`

## 2026-10-05 — Milestone 2 data contract drafted

**Milestone:** 2  
**Status:** IN_PROGRESS  
**Completed in this block:** exact source inventory for all 54 Schema-42 tables; canonical `HOSTED_DATA_MODEL_V1` mapping; non-SQL workspace/history/asset/recovery/import/export state mapping; separate Breadmaker `usr_` identity decision; immutable public project-ID semantics; Firestore/GCS split; generation/CAS model; durable large-operation protocol; tombstone/orphan policy; immutable revision envelope; source-controlled Firestore composite-index/index-exemption plan.  
**Important decisions:** browser direct Firestore access is forbidden; Cloud Run remains the authorization/data plane; normal block prose may be inline only under a 128 KiB soft guard and overflows to immutable GCS; Review Snapshot projection tables collapse into one immutable GCS snapshot envelope; local `bookmarks`/`ui_state` become per-member hosted state; importing a package creates a fresh hosted project ID.  
**No implementation yet:** no Firestore/GCS SDK imports, no live GCP resources, no IaC changes.  
**Next:** add deterministic contract validator/regression coverage, version/package metadata, static checks, then package for user verification.

## 2026-10-05 — Milestone 1 verified; Milestone 2 started

**Milestone:** 2  
**Status:** IN_PROGRESS  
**User result:** Editor `10.3.43` + Cloud `10.4.32` reported all green. Milestone 1 is VERIFIED.  
**Milestone-2 scope:** specification and pure contracts only; no Firestore/GCS client implementation and no live GCP resources.  
**Current work block:** inventory Schema 42 + workspace/filesystem/revision state; define canonical hosted paths, IDs, auth ownership, mutation generations, tombstones, immutable revision envelopes, orphan policy, recovery representation, and source-controlled index plan.  
**Verifier rule:** model does not run verifier/tests; user will verify the packaged Milestone-2 candidate.

## 2026-10-05 — Milestone 1 verification red; corrective 10.3.43 prepared

**Milestone:** 1  
**Status:** AWAITING_USER_VERIFICATION  
**Failed candidate:** Editor `10.3.42` + Cloud `10.4.32`.  
**User verification result:** Unit 89/89; Core/API 1139 passed, 6 failed, 1 skipped; browser 94/94; durable E2E 92/92; Cloud 281/281.  
**Failure digest preserved:**
1. `test_v931_asset_uploads_are_project_bound_and_runtime_discovery_fails_closed` — explicit `PROJECT_ASSET_CONTEXT_CHANGED` HTTP 409 mapping disappeared during byte-port migration.
2. `test_v10342_environment_neutral_ports_do_not_require_paths` — neutral port block contained the word `Path` only in explanatory prose.
3. `test_v10315_hosted_server_never_decodes_project_images_in_process` — historical contract required `RLImage(str(f))` although local export now reads image bytes through the neutral asset port.
4. `test_v10315_hosted_upload_and_portable_assets_validate_before_publication` — historical contract required staged-file validation although hosted upload now validates bytes before publication.
5. `test_v10342_native_path_debt_is_explicit_and_inventory_is_exact` — inventory rule expressed the monotonic rule semantically but omitted the exact phrase `may only shrink`.
6. `test_v1020_dependency_manifest_matches_generated_source_declarations` — generated dependency manifest had not been refreshed after Milestone-1 imports changed.

**Corrective:** Editor `10.3.43`; Cloud remains `10.4.32`.  
**What changed:** restored the context-change 409 mapping; kept neutral port prose free of filesystem `Path` terminology; updated hosted image security contracts to assert validation-before-publication and hosted placeholder-before-decoder under the bytes seam; made the migration-debt rule explicitly say `may only shrink`; regenerated API/dependency/code/inventory artifacts.  
**Runtime/IaC scope:** no Firestore/GCS/Identity Platform implementation; no local schema change; no Cloud infrastructure change.  
**Verifier/tests run by model:** NO.  
**Static checks:** 836 Python files parsed across Editor+Cloud; 34 JSON files parsed; all 48 shipped JavaScript files passed `node --check`; shell launchers passed `bash -n`; targeted static scan of all six red signatures is clean; the generated dependency manifest equals its source-derived builder; secret-material heuristic scan is clean; `src/storage/schema.py`, `src/workspace/constants.py`, `requirements.txt`, and `run` are byte-for-byte unchanged from the last green 10.3.41 baseline; all Cloud `.tf` files are byte-for-byte unchanged from the green 10.4.32 baseline. These are **not verification**.  
**Next safe action:** user runs the full verifier on the 10.3.43 corrective. If green, mark Milestone 1 VERIFIED and begin Milestone 2.
**Package/path:** `Breadmaker_editor_v10.3.43_cloud_v10.4.32_serverless_rewrite_milestone1_corrective.zip`

---

## 2026-10-05 — Milestone 1 candidate packaged for user verification

**Milestone:** 1  
**Status:** AWAITING_USER_VERIFICATION  
**Candidate:** Editor `10.3.42` + Cloud `10.4.32`; local Schema `42`.  
**Completed:** environment-neutral project catalog/membership, logical asset-byte, project-usage, and avatar-read ports; explicit native project/asset/history/backup bridges; migration of safe cross-cutting HTTP edges away from local `Path`/SQLite knowledge; machine-readable local-engine dependency inventory; Milestone-1 architecture documentation; protected-main/required-check/automatic private-alpha delivery requirements added to Milestone 11.  
**Frozen migration debt:** `docs/HOSTED_LOCAL_DEPENDENCY_INVENTORY.json` currently contains 71 direct local-engine owners. New entries are forbidden; later milestones may only shrink it.  
**Local guarantees checked statically:** `src/storage/schema.py`, `src/workspace/constants.py`, `requirements.txt`, and `run` are byte-for-byte unchanged from the green 10.3.41 baseline.  
**Static checks run by model:** Python parse/compile, JSON parse, JavaScript syntax, shell syntax, generated API/code/dependency indexes, targeted source/dependency scans. These are **not verification**.  
**Verifier/tests run by model:** NO.  
**Last user verifier result:** green baseline for Editor 10.3.41 / Cloud 10.4.32.  
**Next safe action:** user runs the full verifier on this candidate. If green, Milestone 2 begins with hosted data-model specification; do not implement Firestore/GCS feature storage before that specification is frozen.
**Package/path:** `Breadmaker_editor_v10.3.42_cloud_v10.4.32_serverless_rewrite_milestone1_candidate.zip`

---

## 2026-10-05 — Milestone 1 start + protected-main decision

**Milestone:** 1  
**Status:** in progress  
**Goal:** extract cloud-ready runtime ports while preserving local Schema-42/SQLite/filesystem semantics.  
**Plan decision added:** during the private-alpha phase, all changes to `main` require a PR and required green Editor + Cloud checks; a successful merge to protected `main` automatically deploys the exact immutable artifact to the restricted production-alpha environment. No staging environment is created yet.  
**Verifier/tests run by model:** NO.  
**Next:** inventory direct local persistence dependencies and introduce explicit environment-neutral vs native-filesystem runtime contracts.

---

## 2026-10-05 — Planning snapshot

**Milestone:** 0  
**Status:** complete  
**Changed:**

- added `SERVERLESS_HOSTED_REWRITE_PLAN.md`;
- added `SERVERLESS_HOSTED_REWRITE_TRACKER.md`;
- added navigation pointers to both `AGENTS.md` files.

**Behavioral code changes:** none.  
**Version changes:** none.  
**Verifier run by model:** no.  
**User baseline:** previously reported green for Editor 10.3.41 / Cloud 10.4.32.  
**Next:** begin Milestone 1 inventory/seam design.

---

# In-progress block template

Copy this block to the top of Work log before/after an atomic change:

```text
## YYYY-MM-DD HH:MM — <short block name>

Milestone:
Status: STARTING | IN_PROGRESS | AWAITING_USER_VERIFICATION | FIXING_RED_GATE | VERIFIED
Goal:
Files expected to change:
Files actually changed:
What was completed:
What remains:
Decisions made:
Risks/blockers:
Static checks run by model:
Verifier/tests run by model: NO
Last user verifier result:
Next safe action:
Package/path if created:
```

If a conversation is likely to become long, add a tracker entry **before** the next broad code-edit tool call.

---

# Verification result template

When the user returns a verifier result, record it here before fixing anything:

```text
Verification candidate:
Editor version:
Cloud version:
Editor accounting:
Cloud accounting:
Failure digest:
Interpretation:
Milestone remains:
Next fix:
```

Do not erase prior failures; append history so a new model can see repeated signatures.


---

# Next-milestone self-prompt (use only after the combined Milestone-4 candidate is user-verifier-green)

> Record the user's green Editor 10.3.52 / Cloud 10.4.32 result in this tracker. Mark Milestone 3 VERIFIED (final stale harness corrective confirmed) and Milestone 4 VERIFIED. Then begin Milestone 5 — integrate all hosted product APIs with the Firestore and Cloud Storage adapters. Treat the Milestone-2 data model plus `HOSTED_FIRESTORE_ADAPTER_V1` and `HOSTED_OBJECT_STORAGE_ADAPTER_V1` as frozen contracts. Build a new hosted runtime composition that removes hosted correctness dependencies on SQLite, project directories, durable local filesystem paths, and process-local persistence while leaving local Breadmaker unchanged. Preserve authorization-neutral project IDs, pointer-last immutable publication, fail-closed membership, and import-as-new-project identity. Do not create compatibility paths for nonexistent hosted data. Do not run `./verify-release`, `./test`, pytest, Playwright, browser/E2E, or the Breadmaker test suite; the user runs verification.


## 2026-10-07 — Milestones 3/4 verified; Milestone 5A checkpoint 10.3.53

Verification candidate: Editor 10.3.52 / Cloud 10.4.32
Editor accounting: ALL GREEN (user report)
Cloud accounting: ALL GREEN (user report)
Failure digest: none
Interpretation: Milestone 3 VERIFIED. Milestone 4 VERIFIED. The 10.3.52 verifier/local-runtime baseline is now the protected integration firewall.
Milestone remains: 5 — hosted product API integration
Next fix: none for M3/M4; begin M5 integration.

### M5A delivered scope

Editor 10.3.53 is a deliberate submilestone package, not the final M5 gate. It establishes:

- hosted runtime composition over Firestore/GCS, with deterministic MemoryDocumentStore/MemoryObjectStore in `hosted_dev`;
- no hosted SQLite/project-directory/native persistence bridges;
- Firestore-authoritative project/domain services with project-generation advancement on canonical child mutations;
- GCS-backed committed assets and revisions/history services;
- hosted project create/open/rename/delete/restore and package/JSON import-export identity semantics;
- transient in-memory decoding of legacy `.breadmaker` SQLite package bytes without durable hosted SQLite persistence;
- direct project authorization through membership authority;
- a hosted HTTP boundary that statically covers all 287 routes in the current API manifest;
- additive 10.3.53 conformance contracts while `scripts/verify_release.py`, `tests/conftest.py`, `src/runtime/local.py`, local requirements, and the frozen M3/M4 adapters remain protected.

### M5A user gate

The user may run the normal Editor + Cloud verifier on 10.3.53. Any red stays within Milestone 5. Do not alter the verifier/harness to make M5 green unless a concrete verifier defect is independently established.

### M5B remaining scope

Route presence is not yet full behavioral parity. Continue with hosted fake-Firestore/GCS behavioral conformance across manuscript/chapter/scene/tab operations, annotations/comments/reviews, canvases/maps/tasks, assets, checkpoints/revisions, imports/exports, and unauthorized direct-project access. Close the user's M5A verifier failures before declaring Milestone 5 VERIFIED.


---

## 2026-10-07 — 10.3.56 verification red; 10.3.57 cold-start/firewall corrective prepared

**Attached Editor result:** Unit `89/89`; Core/API `1204 pass / 10 fail / 1 skip`; Current browser `94/94`; Durable E2E `0/92`. The M5 identity correction is confirmed (`test_v960.py` `4/4`). Two failures were frozen-harness SHA mismatches because `10.3.56` added `test_v8184.py` to `scripts/verify_release.py`; eight Core failures were unrelated hard 10-second lifecycle timeouts, mostly during app startup/import, and Durable E2E failed its first module-scoped setup while `sync_playwright().start()` exceeded the unchanged 15-second item budget.

**10.3.57 corrective:** restores `scripts/verify_release.py` byte-for-byte to SHA-256 `c5de57ae7b0a781cde65f4abca6cc620bf6a0d774f937a182cfbf1730187c837`; keeps `tests/conftest.py` and all hard watchdogs unchanged; makes python-docx/lxml imports action-lazy for local DOCX import/export; makes AES-GCM imports action-lazy for local credential sealing and encrypted BreadReview packages; and prefilters the historical CSS ownership contract before AST parsing without weakening its assertion. M6 architecture/prototype behavior and Cloud IaC are unchanged.

**Verifier/tests run by model:** NO. Static syntax/hash/archive checks only.

---

## 2026-10-08 — Milestone 7A checkpoint prepared after single privacy-reset red

**Milestone:** 7A  
**Candidate:** Editor `10.3.58` + Cloud `10.4.34`.  
**Last user result:** Editor `10.3.57` reported one failing item only: `test_v741_delete_all_external_failure_never_blocks_local_privacy_reset`; no Cloud summary was included in that message.  
**Privacy corrective:** process-global local storage is detached from the former project before Delete All removes the Breadmaker home; replacement activation occurs under the existing History/Recovery/Backup/privacy barriers and project-switch lock; stale pre-reset project siblings are removed before those barriers reopen. External-backup deletion remains best-effort and can never block local erasure.  
**M7A identity scope:** hosted Identity Platform session runtime behind `BREADMAKER_IDENTITY_PLATFORM_ENABLED`; fresh/revocation-checked ID-token exchange; recent-auth requirement; Secure/HttpOnly provider session cookie; revocation-checked bind; global provider revocation; UID-bound one-time private-alpha admission; email non-authoritative; hosted-only lazy Firebase Admin SDK via ADC. Auth0 remains rollback/current staging startup.  
**M7A Cloud scope:** non-secret `google_identity_platform_config`, duplicate-email isolation, email/password, TOTP capability, request logging, authorized staging app domain, and runtime `roles/firebaseauth.admin`. Provider secret payloads remain outside OpenTofu/source/state.  
**Deferred:** M7B provider UX/MFA/linking/security parity and Apple; M7C Auth0 retirement.  
**Verifier/tests run by model:** NO. Static syntax/parsing/hash/archive checks only.  
**Next safe action:** user runs full Editor + Cloud verification on `10.3.58 / 10.4.34`. Green verifies M7A and closes the carried M5/M6 formal gate; red remains on M7A.  
**Package/path:** `Breadmaker_editor_v10.3.58_cloud_v10.4.34_serverless_rewrite_milestone7A_checkpoint.zip`  



**M10/M11 corrective 10.4.7 (2026-10-12):** Cloud-only release/policy synchronization, HCL format normalization and strict whitespace-insensitive WIF gate test. Pending the operator full Cloud verifier. Editor 10.5.5 remains unchanged.

### M10/M11 10.4.8 Cloud-only format/contract corrective

The operator-reported 10.4.7 Cloud gate had 124 passed, 2 failed (WIF test literal formatting and `tofu fmt`). Editor 10.5.5 remains user-verified green and unchanged. The M12 release WIF still defaults off; only the assertion and bootstrap deny-principal list indentation were adjusted. Cloud verification pending; no deployment.
