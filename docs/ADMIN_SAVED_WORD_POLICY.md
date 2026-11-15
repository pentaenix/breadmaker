# Inert backend word-exclusion policy candidate

`saved_word_policy.py` owns the pure backend prepare/review/candidate rules. It is
not composed, has no persistent store or endpoint, and performs no policy changes.
Admin should consume its public preview rather than duplicate identity verification.

WordPolicy contains a revision and exactly one pinned WordIdentity per excluded
hosted owner incarnation, maximum32 entries. Distinct provider aliases for the same
hosted owner do not create additional exclusions or consume more slots. Excluding
an already excluded owner through an alias is a no-op preserving the original pin.
Including that owner removes its one entry. A33rd new owner fails before review;
a no-op for an existing owner remains possible at the cap. Duplicate hosted owners
or contradictory repeated external identities are invalid policy snapshots.

prepare_review requires current trusted identity proof, exact policy revision,
explicit include/exclude action and idempotency key. It returns an immutable
server-internal WordReview with a public preview valid for60seconds. Public keys:
schema_version,mode,executable,scope,action,hosted_user_id,currently_excluded,
desired_excluded,changes_policy,expected_before_revision,expected_after_revision,
mapping_fingerprint,idempotency_key,review_fingerprint,invalidate_word_totals,
prepared_at,expires_at. mode is preview_only and executable is false. Provider
identities, timestamps and evidence remain internal; the target hosted ID is
visible to the owner reviewing their analytics filter. Mapping fingerprint is the
actual canonical WordIdentity fingerprint, not an invented binding revision.

candidate_update produces another immutable proposal only. It checks policy
revision, expiry, current mapping and review integrity. Matching committed receipt
replay returns the current policy unchanged, including after intervening policy
changes; conflicting reuse is rejected. Effective changes get a new server-minted
policy revision and must invalidate previous policy-bound totals. No-op reviews
retain the revision. This policy revision is NOT a global coverage epoch.

## Mandatory future execution boundary

A live handler must authenticate the Admin owner and retrieve a server-owned review.
A caller-supplied JSON review, dataclass or recomputed hash is not authenticated
approval and must never be accepted as one. Commit the policy and idempotency
receipt under an atomic current-revision CAS; fence cached totals against that
same policy head. No-op/replay must not reset the existing refresh quota or trigger
an automatic provider scan. This executor/persistence is not implemented.

Current include preparation requires a fresh trusted identity. A separate explicit
stored-entry removal review is still needed for stale/deleted provider accounts
whose fresh mapping can no longer be obtained. Do not claim universal reversibility
or activate policy editing until this path and retention rules are implemented.
No account deletion, membership/permission change, credentials or live reads occur.
