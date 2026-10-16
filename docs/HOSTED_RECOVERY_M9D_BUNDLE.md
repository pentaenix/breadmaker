# M9 Recovery bundle — Scheduled recovery and isolated restoration

**Candidate Editor 10.3.78 / Cloud 10.4.38. USER FULL VERIFIER REQUIRED.**

This bundle builds on user-green Editor 10.3.75 and carries the unverified 10.3.77 sandbox purge corrective. No user-facing API, real GCP scheduler, or destructive Google Cloud worker is enabled here. The local accountless SQLite/Schema 42 runtime is unchanged.

## Scheduled, independently verified hosted-v3 archives

`hosted.recovery_schedule.HostedScheduledRecoveryService` is an **explicit job-invoked service**, not a process timer. An authorized owner configures a project-level policy in separate Firestore metadata (`recovery_schedules/{project_id}`), with enabled state, cadence 24/48/72/168 hours and declared retention of at least 30 days. The retention field is a **desired policy**, not proof of actual GCS bucket retention enforcement. M10 must provision bucket protection and verify IAM/lifecycle policy.

A trusted external job invokes `tick(project_id, authenticated_owner_user_id, now=trusted_UTC)`; the code does not expose this as a public HTTP route. A due tick uses a stable operation name based on project, owner and due instant, calls the existing M9A owner-fenced immutable hosted-v3 snapshot exporter, then checks the archived object and only commits the success receipt after a verified publication. A crash between publish and receipt can retry the same immutable object. Firestore's latest project generation is checked again before committing the receipt: an intervening edit makes the cycle fail closed. The next due time is one cadence from the current run, not a burst of missed snapshots after downtime.

`check_last` rechecks exact GCS name, generation, metadata and SHA-256 before considering a previous success valid. Failed reads, provider errors, changed project generation and invalid policy produce exceptions, never synthetic success. The recovery archive service remains create/read only; no automatic pruning, account cleanup or bucket deletion.

The class deliberately does **not** enumerate users/projects, accept bearer credentials, register Cloud Scheduler jobs, or run in an in-process background thread. M10 supplies privileged project enumeration, trusted identity bindings, job IAM and Cloud Scheduler -> Cloud Run Job wiring. The deployment/runbook must alert on a missing verified backup beyond the configured RPO and repeated scheduled-job failures; Firestore receipt metadata alone is not evidence of bytes being restorable.

## Disaster-recovery drills, not just checksum verification

`hosted.recovery_drills.HostedRecoveryDrillService` verifies the independent immutable archive and **uses the real hosted-v3 package importer** in wholly fresh `MemoryDocumentStore` / `MemoryObjectStore` providers. It then re-exports the fresh project and compares canonical entity identities and author data (ignoring only the deliberately new root project id), and exact attached asset names/digests. It fails closed on unsupported entity groups, non-representable data, corrupt member/asset digests or semantic mismatch. The test does not mutate or create authority in any real hosted project or bucket. `offline_drill` reads recovery metadata/bytes without requiring the original live Firestore project, simulating source Firestore unavailability, but it must only be invoked by a trusted privileged disaster operator with independent recovery-bucket access.

A memory-only drill proves the hosted import/semantic contract; it **does not** prove that deployed IAM, Google Firestore/GCS consistency, external identity-provider recovery, or live infrastructure replacement is operational. M10/M12 must run audited real provider drills and verify fresh Identity Platform authentication, owner reconciliation and restored import visibility. A full-account disaster needs separate metadata and identity recovery; hosted-v3 recovery archives intentionally do not contain credentials, memberships, account profile, or provider tokens.

## Explicit local portability limitation, audited rather than concealed

`hosted.portability_audit.HostedPortabilityAuditService` exports the authorized generation-fenced hosted snapshot and attempts the strict local Schema-42 conversion *without publishing a download*. It reports a boolean, the exact fail-closed reason, counts, and hashes. This does not fill in absent historical information.

Some frozen Canvas versions, advanced Review snapshots (multiple rounds, missing marker/questionnaire frozen state), and hosted project-shaped checkpoints contain data for which Schema 42 has no equivalent history representation. Those still **must not** become a superficially successful local-v2 export. The complete hosted-v3 recovery path, and its isolated hosted restore, remain the supported lossless recovery format for such projects. True full local-v2 parity will require either changing the stored historical payload at the time of capture plus adding a representable local codec, or an explicit new portable package/storage format and importer. Neither can be solved by dropping history in this bundle. This is an open M9B coverage gap.

## Security, verification, and release boundaries

- Production Firestore/GCS purge, account erasure, and recovery-archive delete remain disabled.
- No live GCP environment has been provisioned by this bundle; resource/IAM/cloud scheduler policies belong to M10.
- No local schema, tests/conftest timeout/watchdog, verifier orchestration, frozen M3/M4 Firestore/GCS adapters, or Cloud 10.4.38 source changes.
- `tests/test_v10378.py` contains credential-free, human-verifier-only checks for due/no-due, restart replay, ownership, independent integrity, failed publication, source Firestore loss, isolated restore, and historical export-blocker reporting. **The assistant must not run these tests or the suite.**
- The 10.3.77 carry-forward was not user-verified at the time of authoring; a recent E2E run suffered a first-fixture Playwright startup timeout and its 92 test setups were recorded as errors from that one cause. No watchdog or runtime browser logic is modified in response.

**Operator action:** run the complete Editor + Cloud verifier on 10.3.78. This is an implementation checkpoint, not production-recovery certification.
