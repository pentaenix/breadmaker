# Working in the Breadmaker codebase

This file applies to code contributors and coding assistants. It describes the current repository rules; the older milestone notes below are retained for context only.

## Before you change anything

1. Read the owning feature's `README.md`, nearby source, and relevant tests. Start at [Architecture](ARCHITECTURE.md) or the [API map](src/api/CODEMAP.md) if you don't know where a change belongs.
2. Keep the **local editor usable with no sign-in, cloud services, or internet connection** once its dependencies are installed. Hosted features must never become a prerequisite for local writing or recovery.
3. Keep the hosted identity, permissions, Firestore, object storage, and recovery boundaries explicit. Browser-supplied IDs are not authorization.
4. Add or update meaningful behavior tests. A source refactor is not a reason to weaken data safety, authentication, or test timeouts.
5. Run [`./verify-release`](verify-release) before proposing a release. CI runs the full Editor gate on pull requests and `main`; the private production pipeline also verifies the Cloud repository before deployment.

## Where changes belong

- `src/api/` — feature HTTP routes and handlers.
- `src/storage/` — local SQLite, commits, integrity, and recovery.
- `src/workspace/` — projects, portable packages, local backup destinations, and credentials.
- `src/hosted/` — hosted storage and recovery adapters.
- `static/js/` and `static/css/` — browser behavior and appearance.
- `scripts/` — generated indexes, release checks, and maintenance commands.

Use `python scripts/generate_api_map.py` and `python scripts/generate_code_index.py` when their respective source indexes need rebuilding. New behavior should have one clear owner; don't create a catch-all module or restore deleted compatibility bridges.

## Releasing

Use `python scripts/sync_editor_release_metadata.py --set-version X.Y.Z` to update the Editor version and browser cache tags together. Review the six protected-file hashes explicitly if those files change: updating a version is **not** approval to change the verifier or security-sensitive source. The release preflight checks both release identity and protected-file integrity.

Do not commit credentials, local manuscripts, generated backups, or private Cloud infrastructure. Pre-alpha storage formats can intentionally reject older data; don't add migration paths unless the product explicitly requires one.

## Old milestone handoffs

The dated notes below include earlier agent/operator instructions and now-retired systems. They are preserved for engineering history and do **not** override the current CI or contributor process.

---

<details>
<summary>View historical agent handoffs (historical engineering record)</summary>

## Editor 10.5.31 — M12S private recovery integrity (OWNER VERIFICATION PENDING)

Operator-only sealed recovery checks live project-scoped GCS metadata before and
after in-memory archive assembly, in addition to Firestore revisions. Encrypted
cross-project restore revokes old Review participant sessions/invitation authority;
unknown participant schema rejects restore before stage/publication. These changes
are opt-in source-only, NOT hosted runtime activation. Cloud 10.4.12 unchanged,
local SQLite/accountless/offline unchanged. Only owner runs ./verify-release.
Canonical tracker: ../SERVERLESS_HOSTED_REWRITE_TRACKER.md

## Editor 10.5.28 — M12N release-identity corrective (OWNER VERIFICATION PENDING)

Owner 10.5.27: Editor 1,677 passed / 28 failed / 1 skipped, 0 timeouts; all
28 failures were stale nested frontend 10.5.26 release markers. Cloud 10.4.12
was owner-green 134/134. CSS logo URL, two Identity Platform imports, and
bootstrap frontend marker are synchronized to 10.5.28. New static version
preflight prevents any long run with mismatched VERSION, HTML, CSS, JS or
Review plan; the three reviewed verifier SHA contracts were updated. Do not
run test suites (user verifies). Local runtime remains offline/accountless.
M12 remains unsigned-off until remaining private-data coverage is completed.
Canonical tracker: ../SERVERLESS_HOSTED_REWRITE_TRACKER.md

## Editor 10.5.27 — M12N verification corrective (OWNER VERIFICATION PENDING)

Owner-run 10.5.26: 1,694 passed / 10 failed / 1 skipped; all 95 Unit,
94 current browser, 92 durable E2E passed; Cloud 10.4.11 owner-green 133/133.
The 10 failures belonged to 6 signatures: three frozen SHA firewalls now
synchronized after reviewing source differences; three newly supported private
record families still tested as unsupported; one unconverted configuration
must fail closed before ID policy; one CRC-invalid image fixture; one stale
Google Storage no-tempfile assertion despite bounded creator-only spooling;
one Review participant injected plaintext test expects its old failure code.
Corrected the assertions to preserve fail-closed security, not weaken codecs.
Editor report and console now END with `Verification target: Breadmaker Editor vX.Y.Z`.
Cloud verifier also prints its exact own version at the END; Cloud 10.4.12
includes a small verifier/report change with a new owner-run gate pending.
Local/offline runtime and per-test timeouts are unchanged. ONLY OWNER runs tests.

## Editor 10.5.26 — M12 consolidated private asset/history recovery (OWNER VERIFICATION PENDING)

This candidate builds on the user-green 10.5.23 baseline, unverified M12L 10.5.24
and experimental M12M 10.5.25 source work. It does NOT enable production
hosted encryption. Paired opt-in codecs now cover private root titles, Review
participants, Cuts, checkpoints, Review snapshots, image assets and revisions
through authenticated private storage. Private asset names are opaque; plaintext
asset/history writes through the legacy adapters must fail before publication.
The sealed operator-only recovery path includes these newly supported families
and can reconcile receipt failure after atomic project+owner publication.
Remaining unsupported project record families explicitly block encrypted archive
publication; no fake 'M12 complete' or real GCP security approval. Local offline
SQLite/runtime, Cloud 10.4.11 and protected verifier/watchdogs unchanged.
Never run tests yourself. Owner alone runs clean Editor ./verify-release and reports
compact digest. Canonical tracker at ../SERVERLESS_HOSTED_REWRITE_TRACKER.md.

## 10.5.25 — M12L experimental encrypted live restore + physical inventory (owner verification pending)
Owner reported Editor 10.5.23 FULL green (digest not pasted) and Cloud 10.4.11 green 133/133. This candidate adds independent project Firestore-tree and physical GCS inventory to sealed source checks, and operator-only live private recovery into an unpublished NEW project that re-encrypts protected Firestore rows and manuscript blocks with destination AAD/KMS. Unsupported hybrids/assets/permission-bearing rows fail closed. The source-only reader manifest now binds whether the archive came from private dual-codec storage, disallowing ordinary-source snapshots in the new private restorer. No HTTP/scheduler wiring, runtime activation, deployment or normal import enablement. Strong remaining blockers: root project/account private metadata, reviewer permissions, history, cuts, checkpoints, assets, physical provider orphan evidence, staged retention/idempotent receipt recovery, live KMS/IAM/real restore. Never run test suites; the owner uses ./verify-release. Local offline/accountless SQLite and Cloud 10.4.11 unchanged. Canonical handoff is the root tracker; this is a source checkpoint, not M12 signoff.

## 10.5.23 — M12K verifier corrective (owner verification pending)
The owner ran Editor 10.5.22: 1,674 passed / 14 failed / 1 skipped, 0 browser retries; all 95 unit tests, 94 current browser tests, 92 durable E2E workflows green, plus previous test discovery and backup fanout failures fixed. Twelve release-contract failures were one stale line in `docs/BREADMAKER_REVIEW_IMPLEMENTATION_PLAN.md`; update that line whenever VERSION changes. One M12G test incorrectly expected chapter encryption to remain excluded despite M12K's opt-in support. One M12K positive recovery test updated an uncreated `b_safe` instead of creating it; it now exercises encrypted create+update and archive readback. Only the two test cases, version/cache markers, Review plan line and handoff are modified. Cloud 10.4.11 unchanged. NO test suite executed by agent; owner runs `./verify-release` on this candidate. Hosted encryption remains opt-in and recovery remains incomplete; normal hosted runtime does not activate encryption. Local app remains offline and accountless.

## 10.5.21 M12K encrypted recovery completeness (owner verification pending)
Never run Breadmaker test suites; human runs the Editor verifier. New project-only `hosted.recovery_coverage` refuses incomplete/unsupported private source snapshots and encrypted models are expanded **only through opt-in injection**. No hosted runtime encryption, no live encrypted restoration, no Google deployment. Accountless local SQLite, backups, writing, and offline use are absolute requirements. Full status is in `../SERVERLESS_HOSTED_REWRITE_TRACKER.md`. Cloud 10.4.11 remains unchanged.

## 10.5.20 — reviewed historical test manifest (2026-10-14)

The M12J 10.5.19 owner run had 1,664 pass, 1 failed unit-discovery test,
1 skip; browser retries all recovered and Cloud 133/133 remained green.
`tests/unit/test_verifier_diagnostics.py` correctly identified a gap: a broad
regex silently hid future-looking test module names. `scripts/verify_release.py`
now uses an exact, reviewed inventory in `scripts/historical_test_modules.txt`.
Adding a previously unseen test MUST cause release verification to fail until
it is incorporated into a real test lane (or consciously reviewed as historical).
Three protected verifier SHA assertions updated after review; per-item watchdogs,
local offline/accountless behavior, hosted storage and Cloud remain unchanged.
**Agent NEVER runs pytest, Playwright, or any verifier**; the human operator
owns test execution. Current handoff: `../READ_THIS_FIRST.md` and
`../SERVERLESS_HOSTED_REWRITE_TRACKER.md`. No new milestone-specific root docs.

## 10.5.19 — M12J verification-security review (2026-10-14)

Canonical current handoff: `../SERVERLESS_HOSTED_REWRITE_TRACKER.md` (read via
`../READ_THIS_FIRST.md`). New quick security smoke, fail-fast test discovery,
all-JavaScript syntax preflight, and tests of verifier-result accounting. The
verifier's reviewed SHA is `f3c186b02bc4d0802abc873cfd8b0fb8824bfc68c4c979cc223477f9f7f3360b`;
all original hard per-item watchdogs and `tests/conftest.py` are unchanged.
Experimental `HostedEncryptedObjectStore` now requires positive authorization;
implicit `None` and other falsey callback results must fail BEFORE touching
GCS/KMS. Human owner exclusively runs verification; never run pytest,
Playwright, `./verify-release` or tests from an agent. Local `./run` is a
first-class offline/accountless product with no Google, network, telemetry or
KMS dependency. No normal-runtime hosted encryption or deployment activation.

## 10.5.18 — M12I source-encrypted, sealed recovery (2026-10-14)
M12I lets ONLY the separately authorized opt-in encrypted recovery writer collect
an ephemeral in-memory hosted-v3 snapshot from a dual-codec protected Firestore
+ GCS source; the independent recovery bucket receives ciphertext exclusively.
Normal public ZIP/JSON/local exports and imports stay blocked in encrypted mode,
and plaintext scheduled backup is unchanged. Source snapshot completeness and
live re-encrypted reimport are NOT implemented. Keep all local/accountless/offline
storage untouched. Human runs tests; agents never run verifier/pytest/Playwright.
Canonical instructions: `../SERVERLESS_HOSTED_REWRITE_TRACKER.md`.

## Editor 10.5.17 — M12H encrypted recovery pilot (2026-10-14)
Opt-in `hosted.encrypted_recovery` writes encrypted chunks to independent recovery
objects and commits an encrypted authenticated manifest last. The isolated reader
must require positive external operator authorization and never require the
original project/Firestore. Existing plaintext scheduled backup job is unchanged;
source-encrypted project exporter is deliberately fail-closed and not yet portable.
Cloud KMS key retention and GCS metadata patch IAM remain unproven. Local `./run`
remains 100% offline/accountless; no GCP, KMS, telemetry or identity dependency.
**Never run `./verify-release`, pytest, Playwright or the test suite as an agent.**
The owner verifies Editor 10.5.17; Cloud 10.4.11 remains unchanged.
Use root `SERVERLESS_HOSTED_REWRITE_TRACKER.md` for current status; do not create
additional root or per-milestone Markdown handoffs.

## CURRENT — 10.5.16 / 10.4.11 — M12G privacy coverage and single-tracker handoff (2026-10-14)

Human confirmed Editor 10.5.15 and Cloud 10.4.11 green. This candidate advances Editor only and is **awaiting human verification**. 12 further Firestore-only user-authored record families are available through **explicit injection only** into `HostedPrivateFirestoreFields`; normal hosted runtime still stores plaintext and encrypted package/backup migration is NOT DONE. Local `./run` stays completely offline/accountless, no cloud/KMS/telemetry dependency. **Agent must never execute any test suite or verify-release**; the owner sends test result digests. Do not change verifier/watchdogs or loosen SHA/protected-file contracts. Only current handoff is `../SERVERLESS_HOSTED_REWRITE_TRACKER.md`; old milestone notes are archived in `docs/archive/`, audit in `docs/security/`, and architecture in `docs/architecture/`. Do not create new per-milestone Markdown files.

## 10.5.15 — M12F current handoff / documentation consolidation
Do not create a new milestone Markdown handoff. Append current status, changes,
blockers and successor instructions to `../SERVERLESS_HOSTED_REWRITE_TRACKER.md`
(the canonical living tracker). `../READ_THIS_FIRST.md` is only a pointer.
Editor 10.5.15 / Cloud 10.4.11 awaits owner verification; last green Editor
10.5.9 / Cloud 10.4.11. The hosted encrypted Tasks/Reviews/Canvas/Maps
pilot remains explicitly injected and INACTIVE in normal runtime. Never run
test suites or verifiers as an agent. Local Breadmaker is fully offline,
accountless, and independent of KMS, telemetry or cloud infrastructure.

## 10.5.14 — M12E exact-SHA firewall corrective
Editor 10.5.13 user verifier had 3 failures: `test_v10353`, `test_v10354`, `test_v10360` all pinned the **old** SHA of `scripts/verify_release.py`. The **only** reviewed difference between 10.5.11 and 10.5.12 was adding `test_v10326.py` and `test_v700.py` to `PREWARM_APP_CORE_MODULES`, no budget/watchdog weakening; unchanged through 10.5.13. Preserve exact SHA contracts but update their digest to the inspected 10.5.12 bytes in 10.5.14. The three test files, Editor versioned asset markers and docs are the only changes; Cloud is unchanged. Human alone runs the complete `./verify-release` with both dependency audits. Never run the suite yourself. Local `./run` must function without account, cloud, internet, KMS or telemetry. See `../READ_THIS_FIRST.md` and `docs/archive/m12_prior_handoffs/M12E_VERIFIER_HASH_REVIEW.md`.

## 10.5.13 — M12E private Firestore opt-in pilot (human verifier pending)

`hosted.security.private_firestore.HostedPrivateFirestoreFields` encrypts user-authored Task/milestone fields only when injected explicitly; never auto-enabled in `src/runtime/hosted.py`. No local auth/network/KMS/metrics dependency. Generic batch and plaintext package portability remain blocked in experimental mode. User alone runs Editor `./verify-release` (two strict dependency audits included). Agent MUST NEVER run pytest/Playwright/verifiers or weaken the 10s/15s per-test watchdog. See `docs/archive/m12_prior_handoffs/M12E_ENCRYPTED_FIRESTORE_HANDOFF.md` and updated root `READ_THIS_FIRST.md`; 10.5.12 had no reported green result and 10.5.13 awaits the user run. Cloud 10.4.11 unchanged.

## 10.5.12 — M12D test setup + milestone focus corrective

Human Editor verification of 10.5.11: no old `encoded` error, but two first-API fixture 10s setup timeouts and one durable `draft_2` visibility race. Only selected modules `test_v10326.py`/`test_v700.py` join pre-existing session-level warmup; preserve `CORE_API_TEST_TIMEOUT_SECONDS = 10.0`, browser 15s, and `tests/conftest.py`. Tasks new-milestone deferred autofocus must not steal focus from a next typed title. Owner alone executes `./verify-release`; agent NEVER runs test suite or Playwright. Local-only remains fully first-class, offline/accountless/no GCP/KMS/metrics. Editor 10.5.12 candidate awaits user verification; Cloud 10.4.11 is owner-verified 133/133 and unchanged. See `../READ_THIS_FIRST.md`.

## 10.5.11 — M12D atomic chapter replacement NameError corrective

The owner ran M12D Editor 10.5.10: 1,593 passed, 7 failed, 1 skipped. All seven failures shared `NameError: encoded is not defined`, affecting six inherited M8B chapter-replacement cases and one new encrypted replacement case. Cloud 10.4.11 is owner-confirmed fully green (133/133), and its files remain unchanged. The original `chapter_mutations.py` M12D edit accidentally omitted `structured, encoded = hybrid` in the `hybrid is not None` branch; 10.5.11 restores that tuple unpacking before immutable publication, preserving both the plain and opt-in encrypted paths. One focused AST guard `tests/test_v10511_chapter_hybrid_contract.py` is added; the existing runtime tests remain unchanged. No suite has been run by the agent; the human must run only Editor `./verify-release` and report the digest. Local runtime/accountless SQLite/backup files and Cloud remain unchanged; hosted encryption remains opt-in, incomplete, and disabled in normal runtime. No GCP deployment, KMS key, hosted telemetry or destructive deletion has been activated. The human runs Editor `./verify-release` only, not the agent. Do not change the verifier/watchdogs, local-first storage or the inherited M8B atomic transaction invariants. See `../READ_THIS_FIRST.md` and `docs/archive/m12_prior_handoffs/M12D_HOSTED_BLOCK_INTEGRATION.md` for handoff.

## 10.5.10 — M12D encrypted block persistence opt-in, NOT activated

The user verified Editor 10.5.9 / Cloud 10.4.11 green. M12D explicitly injects encrypted GCS storage for manuscript_block bodies into hosted domain create/update/get/list and atomic chapter replacement. No host runtime activates it; package import/export fail closed in this mode, Firestore private metadata and archives are NOT fully encrypted. Do not claim E2EE or full hosted privacy. Read `docs/archive/m12_prior_handoffs/M12D_HOSTED_BLOCK_INTEGRATION.md`. NEVER run `./verify-release`, pytest, Playwright, `./test` or full harness: the human user runs tests, agent performs only static checks. Local editor remains fully offline, accountless, SQLite-based, and independent of GCP/KMS/telemetry.

## 10.5.9 — M12C encrypted object publication pilot
Human verified Editor 10.5.8 + Cloud 10.4.11 fully green. This release adds a separate opt-in hosted encrypted immutable-object primitive and privacy regression contracts, NOT an active hosted encryption rollout. Old GCS/Firestore and independent recovery archives still contain plaintext if used. Do not silently replace content-addressed publisher or alter its contracts; use the M12C handoff and maintain coverage inventory. Local `./run`, SQLite, backups and recovery remain accountless, offline and free of KMS/Google services. **Model/agent must NEVER run verify-release, pytest or Playwright.** User runs both suites and supplies digest. See `docs/archive/m12_prior_handoffs/M12C_ENCRYPTED_OBJECT_HANDOFF.md`.

## 10.5.8 — mandatory core + hosted dependency preflight (2026-10-13)
Editor `./verify-release` executes fail-closed `pip-audit --strict` against BOTH `requirements.txt` and `requirements-hosted.txt` BEFORE running the Python test harness. `pip-audit` needs network/advisory access; this does NOT mean GCP deployment or make local `./run` depend on network, account, Google SDK, Cloud KMS, or telemetry. Never skip audit failures, and never run `./verify-release`, pytest, or Playwright as the agent; user executes the verifiers. Editor 10.5.8 / Cloud 10.4.10 candidate is awaiting user results. Source handoff: `../READ_THIS_FIRST.md`. Hosted encryption and metrics are still inactive.

## 10.3.82 — M9 operator integration invariants

Editor 10.3.81 + Cloud 10.4.38 were fully green in the user's combined suite.
For this milestone use read-only Google purge preflight (no success permits),
bounded owner-validated schedule discovery, offline archive-only recovery drills,
and Identity Platform binding inspection with external revocation always pending.
Never route these operators through HTTP, enable production erasure, grant delete
IAM, alter the local SQLite Schema 42, or run tests from the model. The
cross-provider fence, IAM, archive isolation and staging restore must be proven
before enabling irreversible deletion. See `docs/HOSTED_M9_FINAL_OPERATOR_HANDOFF.md`.

## 10.3.81 — Account erasure sandbox + protected SHA digest update

User-verified Editor 10.3.80 ran **92/92** durable browser workflows; the 3
remaining historical tests pin the pre-10.3.80 conftest SHA and must point to
the exact approved 10.3.80 browser-startup change. Retain every SHA assertion,
per-test watchdog and verifier orchestration. New M9C3 `account_sandbox.py`
executes *only* with exact MemoryDocumentStore/MemoryObjectStore drivers;
account-owned projects must complete independent backed-up retained project
purge first. Audit receipts and independent recovery archives are retained.
Collaborations are detached, never erase other owners' projects. Identity
revocation is an explicit external missing step. No HTTP/live Google erasure,
Cloud SDK authority, Schema 42 changes or test-suite execution by model.

## 10.3.80 — Durable verification cold-browser startup correction

Operator-reported 10.3.79 tests were fully green for Unit, Core/API, and current-browser, but the durable module Chromium launch exceeded the FIRST item's original 15s setup/call/teardown budget and cascaded into 92 fixture setup errors. This explicitly scoped 10.3.80 exception to the previously frozen test harness moves shared Chromium startup to `pytest_sessionstart` **only** when `BREADMAKER_DURABLE_VERIFY=1`, under a separate bounded 30s startup and 40s hard stop. Every per-item watchdog and orchestrator remains unchanged. Direct pytest and current-browser tests keep original lifecycles. User alone executes tests. No prod deletion, local persistence, or Cloud change.

## 10.3.76 — M9C2B memory-only purge rehearsal

User confirmed Editor 10.3.75 + Cloud 10.4.38 fully green. M9C2A read-only receipts verified. `src/hosted/purge_sandbox.py` is STRICTLY for exact in-memory fake providers, no live physical deletion, hosted API or Google SDK invocation. Ten conformance tests written, not run. User owns verifier. M9C2C real cloud deletion requires independent coverage/readback and privileged safety review. Keep local runtime, M3/M4 frozen adapters, Cloud and verifier unchanged; synchronize frontend via `scripts/sync_editor_release_metadata.py`.

## 10.3.75 — M9C2A read-only preparation corrective

Keep the canonical project service supplied to `HostedPurgePlanner` available
as `planner.projects`, because `HostedPurgePreparationService` uses it to
construct a verified independent archive reader. Do not introduce a second
source of authority or bypass owner, archive, tombstone, or retention checks.
Physical purge remains disabled; user alone runs the complete verifier.

## 10.3.73 — Release identity synchronization rule

One stale Review-plan current-version marker caused 11 Editor failures in
10.3.72 despite fully green browser/E2E/Cloud gates. When bumping `VERSION`,
use `python scripts/sync_editor_release_metadata.py --set-version X.Y.Z` (atomic bump + all metadata), then `--check`. Exact-version assertions belong ONLY in the centralized release identity validator, not milestone tests.
The script synchronizes current cache, frontend, Review-plan and logo markers
without rewriting historical Review-plan sections. Old source-level milestone
tests must assert a semantically valid minimum release, not pin the latest
patch. `tests/test_v10373.py` is the current synchronized-release regression
contract. Leave verifier, watchdogs, protected runtime and frozen providers
unchanged. No assistant-run test suite. Hosted physical deletion is still
explicitly disabled pending M9C2.

## 10.3.72 — M9C1 corrective (user-verifier pending)

Cloud 10.4.38 green, Editor 10.3.71 failed 21 items, mostly stale test version assertions. Correct 16 source-level release identity assertions to actual release 10.3.72 without changing verifier/watchdogs, frontend release literals, early fail-closed Canvas frozen geometry error, and M9C1 test to count all generated scoped GCS objects. Do not run test suite. An unrelated task milestone E2E timed out once; it is not marked resolved. Do not enable destructive physical purge or relax 30-day retention, owner checks or fail-closed history export. M9B advanced history parity remains outstanding.

## 10.3.67 — M9B1 local portability boundary

Editor 10.3.66 + Cloud 10.4.38 were operator-confirmed all green (M8 and M9A). M9B1 builds hosted-v3 -> local Schema-42 v2 export using an ephemeral in-memory SQLite codec. Never use the local SQLite as hosted authority, never silently drop unsupported entities, never change local import/storage or verifier. Preserve M9A hosted-v3 archive bytes. Full complex Review/Canvas/Map/checkpoint fidelity remains B2. Model never runs tests; user performs combined verification.

## 10.3.66 — M9A cloud recovery foundation

M9 is deliberately broken into M9A (independent sealed recovery packages), M9B (portable and local restore), M9C (deletion/GC) and M9D (scheduling/retention and drills). Keep local Schema 42, M3/M4 adapters, cloud IaC and verification harness frozen. M9A is a hosted-only recovery service with explicit separate GCS-store dependency; a separate injected Python object **does not by itself establish physical bucket isolation**. Do not claim that a backup is fully restorable until M9B tests prove it. No suite execution by the model. The inherited M8B 10.3.65 green gate is still pending user confirmation.

## 10.3.65 — M8B verification corrective

The remaining 10.3.64 failure was a pair of exact, case-sensitive lifecycle-documentation terms. Preserve all M8A/M8B hosted runtime code, protected local behavior, frozen adapters, Cloud 10.4.38 and the user-owned verifier; 10.3.65 changes only the lifecycle wording and current Editor release identities. M8 must be user-verified completely green before M9 starts.

## 10.3.64 — M8B hosted mutation boundary

Preserve the `hosted.chapter_mutations` whole-request replay contract, its transaction read-before-write discipline, conservative Firestore metadata budgets, and the immutable GCS pointer-last rule. Never fall back to partially applied CRUD loops on hosted bulk operations. `HostedRateLimiter` is per-instance load shedding only; M10 requires upstream distributed controls. Keep local SQLite/Schema 42, frozen M3/M4 adapters and the user-owned test harness unchanged.

## 10.3.63 — M8A verification corrective

Preserve the 10.3.62 M8A implementation and the frozen verifier/watchdogs, local runtime, and M3/M4 drivers. Keep `docs/BREADMAKER_REVIEW_IMPLEMENTATION_PLAN.md` synchronized with the Editor `VERSION` without removing historical plan sections. Do not perform M8B changes until the user's combined Editor + Cloud verification is green.

## 10.3.62 — M8A hosted instance-lifecycle boundary

Do not restore the one-worker guard as an authority check. Firestore/GCS/provider-signed sessions decide correctness across replicas; use request-scoped deterministic operation identities for retryable creates, not process memory. M8A is not the whole M8: bulk/multi-step mutation atomicity is M8B. Never change the verifier/watchdogs, local runtime, or frozen M3/M4 drivers. Local Breadmaker stays accountless and cloud-free.

## 10.3.61 — Current hosted identity boundary

Identity Platform is the sole real hosted authentication authority. Do not reintroduce live Auth0 runtime/configuration, email-based merging, persistent browser provider tokens, provider-secret payloads in OpenTofu/runtime, or cloud identity into local `./run`. Preserve the historical engineering contracts below unless a dedicated milestone explicitly changes them. M8 owns Cloud Run/horizontal safety.

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

## M12B non-negotiable local-first security boundary (10.5.7)
Local `./run` is first class, accountless/offline and must NEVER require GCP, Cloud KMS, credentials, IAM, network or metrics. Hosted security codecs are staged, not enabled. Model never runs tests; user owns both verifiers. M12B handoff: `../READ_THIS_FIRST.md`.

</details>
