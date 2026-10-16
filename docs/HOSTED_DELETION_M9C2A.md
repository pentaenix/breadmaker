# Milestone 9C2A — Exhaustive discovery and durable purge preparation

**Original checkpoint:** Editor 10.3.74 / Cloud 10.4.38. **Corrective:** Editor 10.3.75; user verifier pending.

The 10.3.74 full user verifier found five failures from the missing
`HostedPurgePlanner.projects` constructor reference. The planner now retains
the original, already injected hosted project service for independent backup
verification in `HostedPurgePreparationService`. The M9C2A safety behavior and
non-destructive boundary remain unchanged.

This release does **not** permit physical purge. It is a safety checkpoint between
M9C1's read-only known-entity preview and a separately verified, eventually
privileged destructive execution job. Account deletion is also disabled.

## New hosted-only administrative discovery boundary

`src/hosted/purge_discovery.py` introduces `ProjectTreeDiscovery` without
modifying the frozen application Firestore contracts/drivers. Two providers:

- `MemoryProjectTreeDiscovery` performs a one-lock inventory of all project
  document keys, including unknown/previously unregistered collections. Revision identity
  for every present document is included in the fingerprint. It
  identifies missing ancestors of retained nested descendants explicitly.
- `GoogleProjectTreeDiscovery` uses the Firestore administrative `list_documents`
  RPC **with `show_missing=True`**, recursively discovering every child
  collection through `DocumentReference.collections()`. Its streamed/paged
  responses must be exhausted without error. It refuses absent RPC support,
  off-project paths, duplicate paths, and work exceeding the fixed entry budget.

The direct Google SDK boundary is hosted-only and must not be used in the local
runtime. Firestore's normal indexed collection queries are not a valid substitute:
queries can omit missing parent documents that still have descendants.

`HostedPurgePlanner(..., discovery=provider).preview(...)` can now report
`document_inventory_exhaustive=True` only after a complete provider scan.
Without a discovery provider it remains the previous conservative M9C1
`document_inventory_exhaustive=False`. Both cases always report
`physical_purge_authorized=False`.

**Important consistency limit:** an exhaustive traversal is not a globally
transactional, instant-in-time snapshot of Firestore and GCS. A root generation plus per-document revision
check can detect changes to scanned documents, but it is not proof that an independently
mutated orphan or unreferenced GCS object did not change during traversal. No
physical deletion may rely on the inventory alone.

## Durable preparation receipts

`src/hosted/purge_receipts.py` introduces
`HostedPurgePreparationService.prepare(...)` and `resume_preview(...)`:

- Rejects missing deletion tombstone, non-owner callers, less than 30 days of
  retention, or a scanner that cannot establish full coverage.
- Requires an **independent, SHA-verified hosted-v3 recovery archive** owned by
  the project owner for the generation immediately preceding the tombstone.
- Stores a single project-bound Firestore receipt at `purge_preparations/{id}`
  outside the project tree. The receipt binds the owner, idempotency key hash,
  tombstone timestamp, root generation, exact discovery fingerprint, archive
  GCS generation and SHA-256. A different key or changed inventory is rejected.
- A fresh process may replay the identical receipt, but only after rechecking
  ownership, retention, inventory, and archive integrity.
- The cursor remains **zero** and state is always `prepared_read_only`; there
  is no method to advance it, delete content/Firestore documents, or remove
  recovery objects. Preparing a receipt does not consent to erasure.

The independent recovery bucket's deployment and deletion-resistant IAM are
still M10. Scheduled backup execution and real recovery drills are M9D.

## Remaining before M9C2B or any destructive capability

1. Prove real Google traversal against missing-parent descendants and paginated
   collections in a disposable project; credentials are not used in this release.
2. Freeze and revalidate scopes at every **destructive** batch, including
   per-object GCS generation conditions and per-document Firestore update times.
3. Implement idempotent, bounded batch execution/cursors with crash injection,
   lease/claim ownership, and a dry-run/recovery halt switch. An old receipt
   must never become an unconditional deletion token.
4. Document the writer's recovery path, independent backup retention, and
   project-vs-account deletion flow. Keep user identity, avatars, imports,
   shared-project ownership and separate archives out of project purge.
5. Keep the destructive worker disabled until the full user-operated verifier,
   real restore drill, and security review succeed. No live deployment exists.

## Verification

Ten new credential-free conformance checks are authored in
`tests/test_v10374.py`: missing-parent/unknown collections, conservative fallback,
backup requirement, cross-runtime idempotent receipts, inventory-drift refusal,
owner/retention restrictions, inventory budget, and a fake Google
admin-RPC traversal that verifies `show_missing` plus missing-capability refusal.
  A nested orphan-content change without a root-generation bump must invalidate
  an existing receipt. **The assistant did not
execute the test suite.** Run the full Editor + Cloud verifier yourself. M9B
complex hosted Review/Canvas/checkpoint history remains explicitly fail-closed.
