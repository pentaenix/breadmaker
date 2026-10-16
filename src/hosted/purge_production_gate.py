from __future__ import annotations

"""M9C2C pre-deployment production deletion gap assessment; NOT authority.

None of these attestations is currently established by an isolated memory
rehearsal. An operator cannot enable erasure by passing a flag or forging a
receipt: this module contains no GCP mutator and never issues an authorization.
"""
from dataclasses import dataclass

REQUIRED_PROOFS = (
    'dedicated_non_http_worker_identity',
    'scoped_firestore_and_gcs_delete_iam',
    'immutable_recovery_bucket_separate_from_worker',
    'exhaustive_orphan_aware_live_discovery',
    'cross_provider_write_fence_enforced_by_all_mutators',
    'bounded_sealed_manifest_shards',
    'generation_conditioned_replay_and_lease',
    'predelete_verified_independent_restorable_archive',
    'retention_and_owner_checks_at_every_batch',
    'append_only_external_audit_receipts',
    'live_google_disaster_recovery_and_crash_drill',
)


@dataclass(frozen=True)
class ProductionPurgeAssessment:
    missing_proofs: tuple[str, ...]
    production_deletion_authorized: bool = False
    provider_ready: bool = False


class HostedProductionPurgeGate:
    """Evidence gap checklist until M10/M12 GCP operator verification.

    Real evidence requires deployed IAM and runtime attestations independently
    checked by an operator. In-memory flags are never a substitute and this
    method intentionally has no success/allow branch.
    """

    @staticmethod
    def assess(*, recorded_proofs: frozenset[str] = frozenset()) -> ProductionPurgeAssessment:
        # A supplied list is diagnostic only, never execution permission.
        if not isinstance(recorded_proofs, frozenset) or any(not isinstance(x, str) for x in recorded_proofs):
            raise ValueError('HOSTED_PURGE_EVIDENCE_INVALID')
        return ProductionPurgeAssessment(tuple(proof for proof in REQUIRED_PROOFS if proof not in recorded_proofs))
