# M12D — Opt-in encrypted manuscript-block persistence

**Corrective candidate:** Editor 10.5.11 / Cloud 10.4.11. **Last fully user-verified green baseline:** Editor 10.5.9 / Cloud 10.4.11. The owner ran 10.5.10 and reported seven same-signature Core/API failures; 10.5.11 requires new owner verification. Agents MUST NOT run `./verify-release`, `pytest`, Playwright, `./test`, or wrappers that execute tests. Human runs the suite and provides the concise failure digest. No GCP deployment or live IAM change exists.

## 10.5.11 corrective, source failure analysis

The owner ran M12D Editor 10.5.10: 1,593 passed, 7 failed, 1 skipped. All seven failures shared `NameError: encoded is not defined`, affecting six inherited M8B chapter-replacement cases and one new encrypted replacement case. Cloud 10.4.11 is owner-confirmed fully green (133/133), and its files remain unchanged. The original `chapter_mutations.py` M12D edit accidentally omitted `structured, encoded = hybrid` in the `hybrid is not None` branch; 10.5.11 restores that tuple unpacking before immutable publication, preserving both the plain and opt-in encrypted paths. One focused AST guard `tests/test_v10511_chapter_hybrid_contract.py` is added; the existing runtime tests remain unchanged. No suite has been run by the agent; the human must run only Editor `./verify-release` and report the digest. Local runtime/accountless SQLite/backup files and Cloud remain unchanged; hosted encryption remains opt-in, incomplete, and disabled in normal runtime. No GCP deployment, KMS key, hosted telemetry or destructive deletion has been activated.

The missing tuple assignment was present in the green 10.5.9 legacy branch; it was accidentally lost while adding encrypted publication parameters and metadata validation in 10.5.10. Restoring it lets `structured` reach the Firestore payload and `encoded` reach the immutable-object publisher. Inherited atomicity and replay contracts must remain intact. Do not change the verifier, tests' timeout, or weaken any security gates to make these cases pass.

## Delivered scope

- The M12C AES-256-GCM + wrapped-DEK encrypted-object writer is now **explicitly injectable** into `HostedCloudProjectRepository` and `HostedDomainStore` for the **manuscript_block hybrid body only**. Normal project seed/create, subsequent block CRUD, list, and atomic `replace_chapter_blocks` use GCS ciphertext and a generation-pinned Firestore pointer when the injected writer is supplied. Without the injected writer, the old hosted path behaves as before.
- Authorization uses the existing canonical Firestore project/entity access gate BEFORE encryption, KMS or object publication. Readback after stored generation pinning uses M12C authenticated-context AES-GCM. Wrong user, project, block, operation, metadata, hash, generation, or key fails closed. The object path uses operation-scoped opaque IDs, not SHA of manuscript content.
- In the opted-in block path, Firestore `_bm_*_fingerprint` values derive from the ciphertext pointer and operational metadata rather than a direct unkeyed SHA of private manuscript text. The atomic chapter receipt uses a ciphertext-pointer-derived digest for the same reason.
- Firestore pointer-last with atomic project-generation advance remains; GCS orphans are possible after failed transactions and must remain subject to bounded orphan discovery/retention. No GCS deletion capability added.
- Non-encrypted/legacy block pointers cannot be read in this opted-in mode. Existing hosted plaintext is **not migrated**: pre-alpha storage breakage is acceptable, no real customer data exists. Invalid/missing private block payloads fail closed.
- Portable package export and import **reject** this experimental encrypted mode. They would otherwise create plaintext archives or stage records before legitimate membership exists. The recovery writer builds on those exports, so it cannot run a private encrypted backup in this mode yet. These are explicit feature blockers, not release-ready functionality.
- Nothing opts into this mode in the actual hosted runtime. It is available for programmatic integration tests and architecture hardening only. Metrics collection remains off. No claims of fully encrypted hosted customer projects.

## Exact security limitations / NOT DONE

**This is NOT complete encryption.** Firestore project/chapter/tab names, labels, annotations, Reviews, Canvas, Maps, member data, other structured objects, revision history, independent GCS assets, the old content-addressed GCS paths, and the production recovery archive path remain unencrypted under the ordinary runtime. Some structured block metadata can still contain user-controlled strings; field-level allowlisting/classification needs completion. The opted-in block writer does not enable encryption on any automatically started service. The full `HOSTED_PRIVACY_COVERAGE_M12B.json` inventory intentionally retains `NOT_INTEGRATED` and `auto_activate=false`.

Backups/export/import/restore have not been reconciled for ciphertext. Do NOT activate this experimental injection on production or staging without complete archive portability, KMS key retention, runtime-wide format enforcement, metrics/tenant policy and live recovery proof. Google KMS outage means fail closed/unavailable, NEVER fallback to plaintext. KMS key material and GCP credentials must never be required for local mode.

## Files changed

- `breadmaker/src/hosted/domain_store.py`: optional encrypted block publication and hydrated reads (with canonical identity context), no hidden plaintext fallback.
- `breadmaker/src/hosted/chapter_mutations.py`: atomic replacement uses encryption writer when injected and receipt hashes only encrypted pointers in that mode.
- `breadmaker/src/hosted/runtime_services.py`: optional injection in hosted project repository, **not configured in real runtime**.
- `breadmaker/src/hosted/packages.py`: explicit fail-closed package export/import boundary for experimental encrypted mode.
- `breadmaker/tests/test_v10510_private_block_domain.py`: new source and runtime contracts authored, **not run by agent**.
- Privacy inventory, `READ_THIS_FIRST.md`, plan, tracker, security report, release/version metadata and agent instructions updated. `breadmaker-cloud/` and local SQLite Schema42 remain byte-identical to green baseline.

## Next engineering pass before M13

1. **Model-level encrypted Firestore fields:** classify safe server-queryable metadata vs all private fields (including titles, rich review data and free-text). Add authenticated `ciphertext_payload` per model. No permissive unknown-field passthrough; reject plaintext in the persisted record. Consider separate query indexes only when the server can be trusted to observe their metadata.
2. **Full hosted storage families:** assets/images, history/revisions, comment/review/map/canvas, GCS naming, imports/exports. Design server-visible references and stable operation keys without unkeyed plaintext-derived hashes.
3. **Recovery redesign:** explicitly encrypted immutable archives (bounded streaming for large archives), restore with original or rotated KEKs, isolated recovery credentials, orphan cleanup/retention; real live restore still belongs to M13. Package import currently rejects the experiment; fix this before any activation.
4. **Operational hardening:** distributed rate/abuse limiting, strict tenant scoping, backup deletion inventory, image and dependency scans, audit logs without private data. Keep production deletion disabled.

## Operator verification

Run the existing `breadmaker/verify-release` (includes fail-closed core + hosted dependency audits). Cloud repository is unchanged, so its green 10.4.11 verifier remains applicable. If any check fails, send the compact digest. The model **must never** run these suites or claim green without owner confirmation.

## Local is not hosted

`./run`, offline editing, SQLite files, local backups and restoration remain a standalone, fully supported first-class product. No account, network, KMS, GCP project, IAM, telemetry, or hosted dependency installation is needed for local operation. This rule supersedes convenience for hosted implementation.

## 2026-10-13 verification follow-up — Editor 10.5.12

Editor 10.5.11 eliminated all seven `encoded` NameError regressions, but the owner still observed two 10-second setup timeouts in cold-load API modules and one durable E2E failure creating second draft milestone. Editor 10.5.12 changes the existing per-module prewarm selection and guards stale `requestAnimationFrame` description focus when the user is typing another milestone. No 10s/15s timeout budgets are extended. No hosted encryption switch, storage driver or Cloud infrastructure changed; source/contracts are NOT verified until the owner reruns the Editor suite.
