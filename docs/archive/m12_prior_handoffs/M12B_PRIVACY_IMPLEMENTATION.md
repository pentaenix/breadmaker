## 2026-10-13 — M12E private Firestore Task/milestone encryption pilot (Editor 10.5.13 / Cloud 10.4.11)

Added hosted-only `HostedPrivateFirestoreFields` backed by existing AES-256-GCM/KMS envelope, explicitly injectable through hosted DomainStore and CloudProjectRepository for `task` and `task_milestone`. The user-defined record fields become ciphertext, operational IDs/timestamps/generation remain visible, tenant AAD binds project+entity+record. Normal hosted runtime still DOES NOT ENABLE encryption. Generic protected batch and project-package paths fail closed; authorized bounded list filtering occurs only after decryption. Eight targeted conformance cases authored and NOT run. No local SQLite/runtime/backups/GCP/Cloud files changed. `M12E_ENCRYPTED_FIRESTORE_HANDOFF.md` and coverage JSON document exact boundaries, unknown risks and next steps. Do not claim whole hosted data is encrypted. Editor 10.5.12 verification remained pending when M12E work began; human must run Editor 10.5.13 verifier (which also runs both strict dependency audits). Model must NEVER execute test suite.

# M12B Privacy implementation — classification, gap analysis, and release gates

**Status:** CODE-ONLY STAGING; NOT ENCRYPTING HOSTED CUSTOMER DATA YET. Source audit is not a third-party security audit, a pen test, or SOC 2 compliance.

## Three explicit data planes

1. Local-first Editor: local files + SQLite, no user account, no cloud API, no KMS dependency, no metrics emission. Remains a supported standalone product. Its storage is owned/controlled by the user and OS.
2. Hosted private data: manuscript text, project names, Reviews, Canvas, Maps, annotations, commentary, media, exports and historical revisions. Must be AES-256-GCM-encrypted before Firestore/GCS persistence when future activation occurs; context binds project/record; KMS wraps independent DEK. Avoid plaintext in indexed fields, metadata, logs, GCS names and SHA/dedup. The hosted service **can decrypt** with authorized IAM (not E2EE).
3. Hosted metrics: minimum account-linked DAILY numeric counts, separate `users/<uid>/usage_daily/<date>`, no titles/snippets/event-by-event keystroke tracking. Explicit source legitimacy and user rights/retention policy before enabling. No metrics upload exists in candidate.

## Security constraints of proposed envelope

- Google KMS holds KEK (CryptoKey) and never sees manuscript bytes; application creates one independent random 32-byte DEK and AES-GCM 12-byte nonce for every encrypted payload.
- AAD authenticates project_id, entity and record_id. M12B codec keeps these as a fixed ordered JSON context; record tampering or transplanting fails decryption.
- No local key caching or filesystem secret files introduced. An opt-in *inactive* Terraform KMS resource exists, but no production key resource or IAM grants have been applied. KMS errors are structural; no plaintext downgrade; format version fixed.
- Bounded payload maximum 16 MiB. Larger streamed assets need a separately reviewed chunked encryption format with per-chunk AAD/nonce, authenticated manifest, exact generation checks and bounded streams. Do not split AES-GCM chunks without such a design.
- A single KMS key per hosted environment initially, with distinct DEKs. Key rotations don't automatically rewrap old DEKs; maintain old key versions until restores are proven. Cloud KMS failures block hosted decryption.
- An account-owning user's legitimate deletion may be limited by legal retention/independent archives. Never promise instantaneous physical erasure while recovery copies exist.

## Coverage blockers

`breadmaker/docs/HOSTED_PRIVACY_COVERAGE_M12B.json` lists EVERY current Firestore logical model plus GCS storage family as `NOT_INTEGRATED`. The on/off activation state is explicit false. Requirement: every private field + hybrid object + archive path must have an authenticated-encryption read/write/backup contract, and tests inspect actual stored bytes for absence of plaintext. The current hosted data layer writes plaintext. DO NOT flip a global env toggle and call hosted encrypted.

**Query design warning:** A blanket Firestore blob wrapper breaks indexed queries, joins, CAS semantics, package exports and authorization fields. Separate server-visible operational fields from encrypted private payloads and document leaks they permit (counts, timestamps, membership IDs). The GCS object key helpers currently use SHA of plaintext; encrypted bytes alone do not remove this indirect leak.

## Tenant-read hardening included

`http_compat._locate` no longer queries global collection groups to find a nested child. It enumerates only `projects/<pid>` ancestors under a 2,048-item limit, confirms canonical paths and defers to existing `_get()` authorization. An overly large project returns `HOSTED_ENTITY_LOOKUP_REQUIRES_PARENT`; next revision should introduce a scoped O(1) parent lookup index with authoritative parent validation. This is conservative, not yet optimized for large writers.

## Source release acceptance (user executes)

Editor 10.5.7 and Cloud 10.4.10 full suites green, hosted requirements `pip-audit` green/triaged, no local-only regressions, two-user tenant probes, bound failure tested, and additional privacy regressions green. Agent may only do syntax/metadata/ZIP integrity. Live effective IAM and recovery evidence belong to M13.

## 2026-10-13: integrated dependency preflight (Editor 10.5.8)

`breadmaker/verify-release` now audits both the core (`requirements.txt`) and the full hosted (`requirements-hosted.txt`) Python dependency graph using `pip-audit --strict` before starting any tests. This is **local verification using the vulnerability-advisory network**, not a GCP operation. It does not install Google SDKs or make the accountless local product network-dependent. Audit failure, advisory-resolution failure, or offline/no-PyPI failure blocks the release gate; use the output to distinguish actual advisories from unavailable services. The standalone hosted audit is optional and is not required after a green verifier. Cloud sources and the optional KMS resource are unchanged. Hosted encryption remains NOT INTEGRATED.

## 2026-10-13 Cloud-only corrective (10.4.11)

Cloud release contract version checks previously pinned 10.4.8 and 10.4.10. They now verify version-policy-README consistency plus minimum M12B release without pinning each new patch. Owner reports green Editor 10.5.8, including hosted audit; Cloud 10.4.11 awaiting owner verification. No encryption activation or Cloud infrastructure changes. Local remains fully offline/accountless.

## M12C additive pilot (2026-10-13)

A separate, opt-in encrypted object publication primitive now exists at `breadmaker/src/hosted/storage/encrypted_objects.py`. Its output uses opaque operation names and hashes ciphertext, not manuscript bytes; replays are authenticated/decrypted and compared. This is not wired into live provider composition and does not affect the `NOT_INTEGRATED` model coverage. See `M12C_ENCRYPTED_OBJECT_HANDOFF.md`; local remains independent.

## 2026-10-13 M12D addendum

The encrypted-object primitive is now explicitly injectable into `HostedDomainStore`/`HostedCloudProjectRepository` for manuscript-block GCS body persistence and atomic chapter replacement (including reader hydration and server-owned metadata). In the experimental mode, package import/export fails closed, and no runtime automatically instantiates the encrypted writer. This only proves a selected GCS content family can use authenticated ciphertext; other persisted private Firestore/GCS data and archives are not encrypted. The authoritative handoff is `M12D_HOSTED_BLOCK_INTEGRATION.md`.

## 2026-10-13 — M12D correction and verification handoff

The owner ran M12D Editor 10.5.10: 1,593 passed, 7 failed, 1 skipped. All seven failures shared `NameError: encoded is not defined`, affecting six inherited M8B chapter-replacement cases and one new encrypted replacement case. Cloud 10.4.11 is owner-confirmed fully green (133/133), and its files remain unchanged. The original `chapter_mutations.py` M12D edit accidentally omitted `structured, encoded = hybrid` in the `hybrid is not None` branch; 10.5.11 restores that tuple unpacking before immutable publication, preserving both the plain and opt-in encrypted paths. One focused AST guard `tests/test_v10511_chapter_hybrid_contract.py` is added; the existing runtime tests remain unchanged. No suite has been run by the agent; the human must run only Editor `./verify-release` and report the digest. Local runtime/accountless SQLite/backup files and Cloud remain unchanged; hosted encryption remains opt-in, incomplete, and disabled in normal runtime. No GCP deployment, KMS key, hosted telemetry or destructive deletion has been activated.

## 2026-10-14 release correction

Editor 10.5.14 fixes stale verifier SHA-256 expectations in three legacy exact-file firewall tests. The permitted verifier code change from 10.5.12 adds only two cold-start modules to existing prewarm registry. No changes to AES-256-GCM, KMS, Firestore/GCS adapters, hosted activation state, local storage, backups, or telemetry. Await human-owned `./verify-release`; see `M12E_VERIFIER_HASH_REVIEW.md`.
