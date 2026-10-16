from __future__ import annotations

"""M12 source privacy boundary for the experimental hosted workspace.

This is a closed-world policy, not a claim that the hosted runtime is deployed
or that Google IAM/KMS has been verified. It classifies *every* registered
project-scoped model before the private codecs may be used. Unknown future
models halt private-mode construction instead of entering plaintext storage.

Private restore transfers supported author content only. Project membership,
identity, operational recovery metadata and backup credentials are not
transferable authority. Legacy checkpoint reference rows are deliberately
unsupported: private checkpoints use encrypted snapshot + project_asset, and
unrecognized legacy rows/blobs make sealed backup fail closed. This is a
pre-alpha contract; there is no automatic legacy schema migration.
"""

from hosted.firestore.contracts import HostedValidationError
from hosted.firestore.model_registry import MODELS
from hosted.security.private_firestore import HostedPrivateFirestoreFields

PROJECT_ROOT = frozenset({'project'})
RECREATED_AUTHORITY = frozenset({
    'project_membership', 'hosted_model_metadata', 'recovery_state',
})
# These features have NO private persistence or encrypted restore adapter.
# Their presence in an existing source project blocks the sealed archive.
DISABLED_PRIVATE_FAMILIES = frozenset({
    'backup_destination_configuration', 'checkpoint_asset_reference',
})
PRIVATE_HYBRIDS = frozenset({
    'manuscript_block', 'project_asset', 'hosted_revision_history',
    'cut', 'checkpoint', 'review_snapshot',
})
PROJECT_SCOPE = 'projects/{project_id}'


def audit_private_model_coverage() -> tuple[str, ...]:
    """Validate the complete registered namespace; no silent new exceptions."""
    candidates = {logical: model for logical, model in MODELS.items()
                  if model.path_template == PROJECT_SCOPE or
                  model.path_template.startswith(PROJECT_SCOPE + '/')}
    special = PROJECT_ROOT | RECREATED_AUTHORITY | DISABLED_PRIVATE_FAMILIES
    if not special <= candidates.keys() or any(
        candidates[name].authorization_policy != 'system_only'
        for name in ('hosted_model_metadata', 'recovery_state', 'checkpoint_asset_reference')
    ) or candidates['backup_destination_configuration'].authorization_policy != 'project_owner':
        raise HostedValidationError('HOSTED_PRIVATE_MODEL_POLICY_DRIFT')
    required_special_paths = {
        'backup_destination_configuration': 'projects/{project_id}/backup_destinations/{destination_id}',
        'checkpoint_asset_reference': 'projects/{project_id}/checkpoints/{checkpoint_id}/asset_refs/{asset_ref_id}',
        'project_membership': 'projects/{project_id}/members/{user_id}',
    }
    if any(candidates[name].path_template != template
           for name, template in required_special_paths.items()):
        raise HostedValidationError('HOSTED_PRIVATE_MODEL_POLICY_DRIFT')
    if any(HostedPrivateFirestoreFields.protects(name) for name in special):
        raise HostedValidationError('HOSTED_PRIVATE_MODEL_POLICY_DRIFT')
    if any(candidates[name].hosted_store != 'hybrid' for name in PRIVATE_HYBRIDS):
        raise HostedValidationError('HOSTED_PRIVATE_MODEL_POLICY_DRIFT')
    for logical, model in candidates.items():
        if logical in special:
            continue
        if logical == 'manuscript_block':
            continue  # Explicit encrypted GCS payload and metadata contract.
        if (not HostedPrivateFirestoreFields.protects(logical)
                or model.hosted_store not in {'firestore', 'hybrid'}
                or model.authorization_policy not in {
                    'project_member', 'project_member_or_review_participant',
                }):
            raise HostedValidationError('HOSTED_PRIVATE_MODEL_UNREVIEWED')
        if model.hosted_store == 'hybrid' and logical not in PRIVATE_HYBRIDS:
            raise HostedValidationError('HOSTED_PRIVATE_HYBRID_UNREVIEWED')
    if any(name not in candidates for name in PRIVATE_HYBRIDS):
        raise HostedValidationError('HOSTED_PRIVATE_MODEL_POLICY_DRIFT')
    return tuple(sorted(candidates))


def require_private_entity_supported(logical_entity: str) -> None:
    """Applies equally to create, read, list, update, and delete boundaries."""
    if (logical_entity in PROJECT_ROOT | RECREATED_AUTHORITY | DISABLED_PRIVATE_FAMILIES
            or (logical_entity != 'manuscript_block'
                and not HostedPrivateFirestoreFields.protects(logical_entity))):
        raise HostedValidationError('HOSTED_PRIVATE_UNSUPPORTED_ENTITY')
