> **CURRENT CANDIDATE 2026-10-14:** Editor **10.5.14**, Cloud **10.4.11** (unchanged). Owner-run Editor 10.5.13 found 3 stale protected SHA expectations, all for the verifier's reviewed 10.5.12 prewarm-only adjustment; 10.5.14 corrects only those expected hashes, metadata and handoff docs. The original 10.5.13 pilot details below are retained as historical implementation notes. The agent does not run suites; only user verification can close this candidate. See `M12E_VERIFIER_HASH_REVIEW.md`.

# M12E — Opt-in encrypted Firestore Tasks / milestones (2026-10-13)

**Candidate:** Editor **10.5.13** / Cloud **10.4.11**. **NOT USER-VERIFIED.** Last confirmed ALL-GREEN was Editor **10.5.9** / Cloud **10.4.11**. Editor 10.5.12 was delivered as a verification correction; its test result has **not** been provided. The 10.5.13 bundle includes all 10.5.12 changes, so user should run Editor `./verify-release` on 10.5.13. Cloud 10.4.11 remains byte-identical to last green and need not run again solely for this Editor update.

## Purpose and actual security property

An explicit, off-by-default `HostedPrivateFirestoreFields` injector now permits **two Firestore-only logical families** (`task`, `task_milestone`) to persist user-editable JSON content as AES-256-GCM ciphertext instead of plain Firestore fields. The encryptor uses the existing hosted-only `HostedEnvelopeCodec` with a random DEK wrapped by the selected KMS adapter. AAD binds each record to `{project_id, entity, record_id}` (nested milestones also include their parent task ID in the authenticated record identity); wrong project/entity/record, swapped ciphertext, missing envelope, wrong KMS key, or unexpected plaintext Firestore fields fail closed.

The only Firestore-visible fields in this experimental format are structural identities (`id`, `task_id` or `milestone_id`), generation and timestamps, internal operation receipts and the ciphertext envelope. **All user-defined fields are encrypted by default**, including fields not known in advance. No plaintext SHA-256 of user content is stored as an idempotency receipt. The service validates size (300,000 bytes max plaintext to accommodate Firestore's document cap) and seals before starting a retryable Firestore transaction. Canonical per-project membership authorization precedes reads/decryption.

`HostedDomainStore(..., encrypted_fields=codec)` and `HostedCloudProjectRepository(..., encrypted_fields=codec)` opt in for **deterministic offline testing only**. `src/runtime/hosted.py` never instantiates or injects this codec. Existing local `./run` / SQLite Schema42, files, backups, recovery, and browser workflows do not import or depend on Cloud SDK, key material, network, accounts or telemetry.

## Intentionally unsupported

- Package import/export/preview are fail-closed in experimental mode; no plaintext package bypass.
- Generic atomic batch writes of protected entities are fail-closed until prepared ciphertext batches and replay semantics are verified.
- Encrypted field filtering is **not** performed as a Firestore index query; an authorized, bounded collection query (512 max rows) is decrypted and filtered inside the service. Larger projects require a separate reviewed query design. This bound is not a long-term solution.
- This pilot **does not** encrypt project titles, chapter titles, manuscript metadata, Reviews, Canvas, Maps, comment threads, assets, existing GCS families, snapshots, backups or recovery. `docs/HOSTED_PRIVACY_COVERAGE_M12B.json` retains `encryption_coverage=NOT_INTEGRATED` even for Tasks because ordinary runtime does not opt in.
- KMS key creation, real provider IAM, external recovery proof, distributed rate limiting, hosted metrics collection, and secure deletion remain **M13/live or later M12 implementation tasks**. No GCP deployment occurred.
- Browser or other code which reads Firestore directly rather than through the domain store must not be activated with this pilot. DomainStore is the only authorized plaintext view in the experimental mode.

## Reviewer checklist, user-owned verification

1. Run Editor `./verify-release` from `breadmaker` (includes **core and hosted `pip-audit --strict`**) and report concise digest. **Agent must not run tests**; it may only do syntax checks, version synchronization, ZIP integrity, and diffs.
2. `tests/test_v10513_private_firestore.py` covers ciphertext-only create/read/update/list, nested milestone AAD, authorization before decrypt, ciphertext transplant/legacy plaintext rejection, replay and no plaintext-derived receipt, KMS failure, batch/package bypass guards, and local accountless boundary. New tests are **authored, not run**.
3. If verification is red, fix root causes before proceeding to M12F. The previous 10.5.12 suspected flaky legacy module timeouts and milestone autofocus are **not** treated as green without operator result. Do not weaken 10s/15s watchdogs.
4. Cloud source 10.4.11 is unchanged; no need to re-run Cloud solely for Editor changes.

## Next engineering work (M12F/M12G)

- Create a reviewed per-model classification and encrypted index/query policy for private Firestore content. Expand to chapters, Reviews, Canvas, Maps and other high-risk families while preserving authorization, transaction CAS and package/recovery semantics. Avoid giant global wrappers and ciphertext blind migrations.
- Design and implement isolated *encrypted* archival/recovery without requiring the source Firestore project. Preserve key-version retention until independent recovery proofs pass; ensure backup workers cannot gain content decrypt privileges unnecessarily.
- Audit and replace remaining plaintext-derived GCS object hashes and leaked metadata. Test unknown field rejection/encapsulation and mismatched context systematically.
- Keep `M12_SECURITY_AUDIT.md`, `M12B_PRIVACY_IMPLEMENTATION.md`, coverage JSON, plan/tracker and the root `READ_THIS_FIRST.md` current with every release. Maintain three-part versions; use `scripts/sync_editor_release_metadata.py --write` and `--check` after Editor version bumps. **Do not run the test harness.**
