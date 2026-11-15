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

An explicit prepare_stored_removal path now reviews removal of the exact current
server-stored policy entry even when its provider account is deleted or cannot be
resolved. The internal server-owned review marks this removal-only path; public
preview keys remain unchanged and action is include. Candidate update verifies
both exact policy revision and stored identity fingerprint before removing that
entry. It neither proves a current provider mapping nor acts on an account.
The future UI must label removal of an existing filter accurately and the handler
must not accept a caller-supplied stored_removal flag or reconstruct server-owned
reviews from public JSON. A durable executor and listing/review handler are still
absent. No account deletion, membership/permission change, credentials or live
reads occur.
