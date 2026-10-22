# Hosted data trust boundaries (M12)

- Hosted manuscript, Review, Canvas, Map, asset and revision data are server-readable in Firestore/GCS. There is no client-side end-to-end encryption, so authorized administrators / service identities may access plaintext. Do not call this zero-knowledge.
- Google-managed at-rest encryption and private IAM are not a guarantee against a compromised privileged account. Live IAM must be verified independently.
- M12 removes raw hosted diagnostic exception text, request context and traceback from application Cloud Run stderr. This does not cover third-party logging until M13 live checks.
- Recovery archives are retained separately and may survive project tombstoning or account-closure rehearsals. Production erasure is disabled, and complete erasure cannot currently be promised.
- Hosted-v3 archive restoration is not a blanket guarantee for every account/system document; frozen advanced Review/Canvas histories still have a distinct local Schema 42 portability limitation.
- Local/offline Breadmaker requires no cloud identity or account. Local manuscripts stay on the user's machine; local backup-provider credentials are AES-GCM sealed under a separately stored per-installation owner-only key. Loss of the key prevents decrypting saved remote credentials; the owner OS account is the trust boundary.
- Avoid logging raw manuscript data or identity secrets in error reports, analytics, hosted metrics, crash handlers, release summaries or operator runbooks. Monitoring should use aggregate event codes and bounded counts only.
- Hosted structural actions journal before/after Firestore records atomically with each write. Recovery rechecks actor membership, project generation and exact record state; intervening edits fail closed rather than being overwritten. Journals contain server-readable metadata/pointers, are bounded to 100 actions per user and 100 records/450KB initial journal per action, expire after seven days, and are pruned on the user's next action (not by an always-running job). Restore images add bounded storage to those journals. IAM/session/membership records are never restored. Undo across intervening non-journaled edits requires reopening rather than bypassing the generation guard.

See the `docs/security/M12_SECURITY_AUDIT.md` for source evidence, open issues and the exact M13 operator acceptance gates.
