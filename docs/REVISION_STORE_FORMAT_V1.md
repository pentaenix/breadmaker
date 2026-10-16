# Breadmaker Revision Store Format v1

Breadmaker 7.8.6 keeps the logical Revision History format introduced in 7.7, but the application is now deliberately **current-format-only**. Pre-alpha compatibility and migration code has been removed. A non-empty repository that does not satisfy the current v1 contract fails closed rather than being repaired or reinterpreted.

## Local repository

The local repository lives at:

`revision_history/v1/`

Every current repository requires:

`revision_history/v1/repository.json`

The marker records:

- `store_format = breadmaker-local-revision-store`
- `store_version = 1`
- `snapshot_version = 1`
- `revision_version = 1`
- `checkpoint_version = 1`
- checkpoint interval
- immutable workspace UUID

An empty repository may initialize this marker. A non-empty repository without it is corruption and is not migrated.

### Downgrade/write fence

Unknown store/component versions, missing required identity, or a workspace UUID mismatch fail closed before History publication. Breadmaker never overwrites repository identity to make incompatible bytes appear current.

## Immutable local components

v1 uses:

- snapshot object manifest: `breadmaker-local-revision-snapshot`, version 1
- revision publication manifest: `breadmaker-local-revision`, version 1
- checkpoint manifest: `breadmaker-local-revision-checkpoint`, version 1

A revision publication is the local commit point. Snapshot/checkpoint bytes without a valid revision publication are not History authority.

## Remote History

Remote History remains logical format v1:

- repository claim: `breadmaker-remote-history-repository`, version 1
- writer authority: `breadmaker-remote-history-writer-authority`, version 1
- revision publication: `breadmaker-remote-revision-publication`, version 1

A current repository requires **both** repository authority records. If either one is missing while the other remains, the repository is corrupt and Breadmaker does not reconstruct the missing record. If both are absent, repository genesis may be created only at an explicit new-destination initialization boundary; provider listing emptiness is never proof that an old repository is unused.

Required objects and lineage are uploaded and verified first. The immutable remote revision publication is written last.

Read/verify/local-repair capability does not require the installation's writer identity. Publish and remote GC do.

## History GC

History GC state is `breadmaker-history-gc-state`, version 1. GC state is never revision authority. Unknown or malformed GC state cannot authorize deletion.

GC remains structurally separate from Recovery Backups and cannot address `.breadmaker` Recovery payloads.

## Current `.breadmaker` contract

Breadmaker 7.8.6 supports only the current portable Recovery/project package contract:

- `package_format = breadmaker-project`
- `package_version = 2`
- application schema = 22

Older pre-alpha package/schema versions are intentionally unsupported and fail closed. Breadmaker does not migrate them.

A current `.breadmaker` package remains independently recoverable without Local Revision History, Remote History, GC state, provider credentials, or installation metadata. Checkpoint-owned asset bytes are part of the current schema/package so Checkpoints remain self-contained after Recovery.

## Current v1 invariants

1. Current generation authority only moves forward.
2. Every current History repository is bound to one immutable workspace UUID.
3. A retained local/remote revision reconstructs the canonical state it claims.
4. Revision publication is manifest-last.
5. Unknown/missing format authority fails closed; it is not migrated.
6. Both Remote History authority records are required once initialized.
7. History GC cannot address Recovery Backup packages.
8. Recovery package identity separates canonical logical state from physical package bytes.
9. Cuts are independent current rows; Checkpoints own their referenced immutable asset bytes.
10. Old pre-alpha storage is rejected rather than rewritten.
