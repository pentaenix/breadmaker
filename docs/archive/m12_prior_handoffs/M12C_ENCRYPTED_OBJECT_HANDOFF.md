# M12C — Encrypted hosted object publication pilot

**Candidate:** Editor `10.5.9` / Cloud `10.4.11`. **User-verified baseline:** Editor `10.5.8` and Cloud `10.4.11` (2026-10-13; all gates green). This candidate has NOT been run through the user's tests. The agent MUST NOT execute pytest, `./verify-release`, Playwright or any test harness. Only static checks, syntax parsing, metadata sync and ZIP CRC are authorized for the agent.

## Scope and threat boundary

This is an **opt-in, not-wired** hosted-only GCS object publication boundary, not activation of hosted at-rest application encryption. Local `./run`, SQLite Schema42, offline backup/restore, no-account workflows and no network dependency stay first-class. The cloud project is not deployed; no live IAM, KMS or bucket permissions were changed. `breadmaker-cloud/` is **byte-identical to the user-verified 10.4.11**.

`breadmaker/src/hosted/storage/encrypted_objects.py` introduces `HostedEncryptedObjectStore`, requiring an explicit canonical project membership authorizer and reusing the M12B `HostedEnvelopeCodec` (AES-256-GCM with random DEK/nonce and KMS-wrapped key). It publishes authentic ciphertext to an existing `ObjectStore` through `if_generation_match=0`, then verifies exact stored ciphertext bytes, sha256 and generation before treating publication as successful. Replays with the same operation/name return the original generation only when the authenticated old plaintext equals new plaintext; mismatched payloads raise an error. It exposes no delete method.

The key `projects/{pid}/private/v1/{entity}/{record_id}/{operation_id}.bme` has **no plaintext digest or title**. Contextual AES-GCM AAD binds the project, entity and SHA-256 of the *full canonical object name*, which includes record and operation. Object metadata contains only a format marker and SHA-256 of **ciphertext**, never plaintext. Wrong KMS key, tampered envelope, unexpected generation, size, hash, or swapped reference fails closed. Max plaintext 16 MiB: no ad-hoc chunking of larger archives or images. This code uses an in-memory plaintext buffer during legitimate processing; it is application-managed encryption, **not E2EE**. Backend processes with decrypt permission can access plaintext.

**Important limitation:** project membership authorizer is required, but **per-record user permissions must be independently checked by each integrating service** before calling the primitive. It is NOT appropriate as a general untrusted API. It must never directly accept user-supplied storage references or operation IDs without canonical server ownership checks. The pilot must not be attached automatically underneath the legacy `ObjectStore`/`ImmutableObjectPublisher`, because their current contracts depend on plaintext digest-based keys and pointers. Same caution applies to large mutable objects, asset URL identity, scheduled backups and rich history.

## Existing invariants retained

- No runtime invocation, deployment, IAM change, telemetry upload, automatic encryption or transparent plaintext fallback.
- All Firestore private model entries remain `NOT_INTEGRATED`; hosted assets, block payloads, Cuts, Reviews, Canvas, Maps, imports/exports, history and backup archives are **not yet encrypted by this pilot**.
- Existing Firestore/GCS adapters, local storage, verifiers, watchdogs, KMS blueprint and recovery jobs remain byte-identical (aside from version-facing Editor frontend assets).
- `breadmaker/verify-release` remains strict and audits both core and hosted dependency sets before user-run tests. The agent never runs it.
- Key unavailability is an availability failure; it must NEVER trigger plaintext downgrade. Keys needed by retained backups cannot be destroyed or rotated away until recovery proves readability.

## M12 next source steps (before M13)

1. Design per-model Firestore field classifications. Avoid wrapping indexed/authorization fields; move private text/titles into encrypted payloads, retaining only documented minimal operational metadata. Add old-format rejection (pre-alpha permits breaking schema), and real stored-byte absence tests.
2. Migrate one complete hosted data family with **read, write, retry, export, recovery and deletion** semantics, including actual app integration and cross-tenant negative tests. Pilot from this release is a primitive only, not integrated.
3. Migrate the remaining manuscript/Cuts/Reviews/Canvas/Maps, history, images, imports and exports; replace SHA-derived object names and manifests; preserve stable idempotency without leaking plaintext digests. Keep coverage inventory honest.
4. Ensure independently stored immutable backups can be restored after source loss *and* KMS rotation. Design a retained-key strategy and operator IAM/drill plan. Neither the current recovery writer nor isolated restore job can claim plaintext confidentiality today.
5. Implement authenticated daily metrics with user consent/retention/export/deletion controls, not per-keystroke telemetry. Resolve distributed rate limiting, deletion coverage and dependency/container scan evidence. User-only verification after each substantial bundle.

## M13 remains operator-only

GCP project/billing, KMS key provisioning, GitHub protection/WIF, actual image build/scan, real IAM denial tests, staging/deployment, live multi-user authorization, backup restoration and rollback. No private alpha admission and no SOC 2 compliance claim.

## User verification

From extracted ZIP:

```
cd breadmaker && ./verify-release
cd ../breadmaker-cloud && ./verify-release
```

Send **only compact failure digests**. No separate hosted dependency command is needed after a green Editor `verify-release`; it already scans core and hosted transitive dependencies. No GCP project, account, deployment, KMS key or network required for local app; verifier alone requires internet for advisories.

## M12D follow-up (2026-10-13)

This M12C document describes the original primitive. M12D adds **explicit opt-in domain wiring** for hosted manuscript-block GCS bodies and chapter-replacement paths; see `M12D_HOSTED_BLOCK_INTEGRATION.md`. Still inactive in the normal hosted runtime and not complete Firestore/backup encryption. The user confirmed M12C Editor 10.5.9 / Cloud 10.4.11 GREEN.
