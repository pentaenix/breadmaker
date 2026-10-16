> **Verification update (2026-10-16):** The maintainer reported Editor 10.5.35 and Cloud 10.4.12 green. This accepts the defined M12 *source* scope, not the live hosted encryption or disaster-recovery lifecycle. M13 deployment, IAM, restore drills and real manuscripts require separate operational acceptance. The following text is the earlier 10.5.34 candidate record and retains its historical status wording.

# M12 source closure acceptance — Editor 10.5.34

**Status:** SOURCE CANDIDATE, NOT ACCEPTED until owner reports green full Editor
verification. Cloud 10.4.12 had 134/134 owner passes and is unchanged.
No live service, KMS key, bucket, hosted app, or private manuscript has been deployed.
This is a code-security gate, **not** an encryption or disaster-recovery certification.

## Fixed source boundary, no new M12 feature work

The installed local Breadmaker retains SQLite and all local backups/revisions
with no account, internet, GCP, or KMS dependency. The opt-in hosted private
stack is intentionally **not injected** into the production runtime. The local
and standard hosted runtime have not been migrated to encrypted storage.

For a *fresh experimental private project* with both injected codecs, there
are **52 registered project-scoped families**:

- **45** explicitly authenticated/encrypted Firestore field families, including
  private metadata for hybrid objects, Reviews, scenes, assets, history and
  owner-bound bookmarks/preferences.
- **1** specially encrypted manuscript-block family (GCS authenticated bytes,
  reviewed operational metadata and ciphertext pointer).
- **1** project-root document with independently sealed title.
- **3** authority/operational model families (`project_membership`,
  `hosted_model_metadata`, `recovery_state`) that are not portable and are
  reconstructed as appropriate rather than copied into a new account.
- **2** explicitly disabled legacy/operational families
  (`checkpoint_asset_reference`, `backup_destination_configuration`).
  Private CRUD is denied and an existing nonempty family blocks encrypted
  recovery rather than silently omitting data or transferring a credential.

The private model catalog gate now runs at dual-codec construction and refuses
unknown/changed model families. Generic create/read/list/update/delete follow
the same fail-closed policy. The gate is source-only and checks the model
registry; it does **not** prove live cloud or direct provider permissions.

## Explicit data/authority policies

1. **Novel content, Reviews, saved checkpoints, images, Cuts, revisions and owner
   state** must roundtrip through the sealed archive and a fresh destination-
   identity/key-bound restore. Unknown plaintext, missing pointers, mismatched
   snapshots or unrecognized assets stop publication. Historical checkpoint
   image references must resolve to authenticated portable `project_asset`.
2. **Independent old checkpoint binary blobs and system reference rows** are
   not supported in private mode. There is intentionally **no legacy migration**
   for these pre-alpha formats. Presence blocks backup; do not describe this as
   a successful restoration of an old checkpoint workspace.
3. **Backup destinations and secret-vault configuration** are never imported or
   silently reauthorized. The private domain does not permit persisting the
   unconverted destination configuration family. Operator-owned, fresh
   destination provisioning/authorization is required in M13 before enabling
   scheduled cloud backups. Source recovery uses an independent encrypted
   operator bucket, not restored third-party backup credentials.
4. **Accounts, external identity provider bindings, avatar/profile bytes and
   other user-scoped metadata** are not project archive content. Hosted identity
   remains server-readable operational information, not end-to-end encrypted.
   The new owner must already exist in destination identity storage. Review
   participant *content* is re-encrypted, but old tokens are stripped and all
   participants restored revoked; a participant row is not login authority.
5. **Other members' private state** blocks recovery; it is not copied as the
   new owner's bookmarks. New owner-only membership is created at the final
   atomic authority commit. Unrecognized historical records also fail closed.
6. **Failed restore staging** remains encrypted and inaccessible. Its 30-day
   read-only retention inventory is not a deletion permit. No delete API or
   automatic cleanup is activated. Physical orphan erasure is an M13 operator
   operation, after independent listing and recovery proof.

## Owner verification required to accept M12

- Extract the exact 10.5.34 release to a **clean** directory; run Editor
  `./verify-release` and report the end-of-log version and summary. Do not
  run the test suite from inside an old extracted release.
- Confirm `python scripts/audit_m12_private_source.py` passes its source-only
  registry policy (this check does not contact GCP or execute tests).
- Review the six SHA-pinned protected files and leave their manifest intact;
  compare the latest source against 10.5.33. No protected file changed in this
  10.5.34 candidate.
- Confirm all new private source closure tests are discovered and green. A
  skipped item is not automatically evidence of a privacy problem, but the
  explicitly skipped test must be reviewed before deployment.
- **Do not enable private storage or claim recovery guaranteed for user data.**
  M13 must prove actual provider enumeration, KMS key lifecycle/retention,
  IAM least privilege, encrypted backup/readback/new-project restore after
  original-source loss, provisioning of backup credentials, threat/abuse probes,
  clean rollback and observability/retention controls.

No additional M12 private models or legacy migration are authorized after this
scope freeze. A new critical source defect uncovered by owner verification is
an M12 corrective, not grounds for inventing unrelated features.
