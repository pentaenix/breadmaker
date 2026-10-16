from __future__ import annotations

"""Read-only, operator-authorized inventory for unpublished private restores.

Failed encrypted restores intentionally leave encrypted staged rows and immutable
objects. This planner identifies their scope without deleting anything. A GCP
provider unable to enumerate an orphaned Firestore subtree MUST fail closed;
retention eligibility is not authorization to delete. The independent live
cloud orphan inventory and a destructive lifecycle require a separate M13 gate.

Not wired into HTTP, the scheduler, the local editor or physical erasure.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from hosted.firestore.ids import require_opaque_id, require_project_id, require_user_id
from hosted.firestore.model_registry import MODELS
from hosted.security.envelope import FORMAT
from hosted.storage.keys import require_object_name

_MAX_STAGING_DOCS = 12000
_MAX_STAGING_COLLECTIONS = 1800
_MAX_STAGING_OBJECTS = 12000
_MIN_STAGE_RETENTION_DAYS = 30


class HostedPrivateStagingError(ValueError):
    """Only non-content diagnostic codes are exposed to an operator."""


@dataclass(frozen=True)
class HostedPrivateStagingPlan:
    destination_project_id: str
    restore_operation: str
    document_count: int
    object_count: int
    ciphertext_bytes: int
    staging_started_at: str | None
    retention_eligible: bool
    manual_review_required: bool = True
    deletion_enabled: bool = False


def _utc(value: str) -> datetime:
    if not isinstance(value, str) or len(value) > 48:
        raise HostedPrivateStagingError('HOSTED_PRIVATE_STAGE_TIMESTAMP_INVALID')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        raise HostedPrivateStagingError('HOSTED_PRIVATE_STAGE_TIMESTAMP_INVALID') from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HostedPrivateStagingError('HOSTED_PRIVATE_STAGE_TIMESTAMP_INVALID')
    return parsed.astimezone(timezone.utc)


def _registered_staging_path(path: tuple[str, ...], pid: str) -> bool:
    """No wildcard path deletion: all observed docs must match known models."""
    if path[:2] != ('projects', pid) or len(path) < 4 or len(path) % 2:
        return False
    for model in MODELS.values():
        pattern = model.path_template.split('/')
        if len(path) != len(pattern) or pattern[:2] != ['projects', '{project_id}']:
            continue
        if all((part.startswith('{') and part.endswith('}') and
                isinstance(actual, str) and bool(actual) and '/' not in actual)
               or (part == actual) for part, actual in zip(pattern, path)):
            return model.logical_entity not in {
                'project_membership', 'hosted_model_metadata', 'recovery_state',
                'backup_destination_configuration', 'checkpoint_asset_reference',
            }
    return False


class HostedPrivateRestoreStagingPlanner:
    """Operator-only read path; never writes or deletes records or objects.

    Authorization requires an independent two-argument (operator user, restore
    operation) callback returning literal True. It runs BEFORE receipt, bucket,
    Firestore, or KMS access. Even an eligible result only authorizes review.
    """

    def __init__(self, documents, objects, *, authorize: Callable[[str, str], bool]):
        if not callable(authorize):
            raise HostedPrivateStagingError('HOSTED_PRIVATE_STAGE_AUTH_REQUIRED')
        self.documents = documents
        self.objects = objects
        self.authorize = authorize

    def plan(self, operator_user_id: str, restore_operation: str, *,
             now: datetime, retention_days: int = _MIN_STAGE_RETENTION_DAYS) -> HostedPrivateStagingPlan:
        uid = require_user_id(operator_user_id)
        op = require_opaque_id(restore_operation, label='restore_operation')
        if self.authorize(uid, op) is not True:
            raise PermissionError('HOSTED_PRIVATE_STAGE_OPERATOR_DENIED')
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise HostedPrivateStagingError('HOSTED_PRIVATE_STAGE_CLOCK_INVALID')
        if type(retention_days) is not int or not _MIN_STAGE_RETENTION_DAYS <= retention_days <= 3650:
            raise HostedPrivateStagingError('HOSTED_PRIVATE_STAGE_RETENTION_INVALID')
        receipt = self.documents.get(('hosted_private_restore_operations', op))
        if receipt is None:
            raise HostedPrivateStagingError('HOSTED_PRIVATE_STAGE_RECEIPT_MISSING')
        fields = receipt.data
        if (fields.get('status') != 'staging' or
                fields.get('destination_owner_user_id') != uid):
            raise HostedPrivateStagingError('HOSTED_PRIVATE_STAGE_NOT_UNPUBLISHED')
        try:
            pid = require_project_id(fields.get('destination_project_id'))
            require_project_id(fields.get('source_project_id'))
            require_user_id(fields.get('source_owner_user_id'))
        except (TypeError, ValueError):
            raise HostedPrivateStagingError('HOSTED_PRIVATE_STAGE_RECEIPT_INVALID') from None
        if (not isinstance(fields.get('source_snapshot_id'), str) or
                not isinstance(fields.get('source_manifest_ciphertext_sha256'), str) or
                type(fields.get('restored_entity_count')) is not int or
                fields['restored_entity_count'] < 0):
            raise HostedPrivateStagingError('HOSTED_PRIVATE_STAGE_RECEIPT_INVALID')
        # NEVER plan staged deletion for a project that has gained authority,
        # even if the receipt remains 'staging' after an interrupted commit.
        if self.documents.get(('projects', pid)) is not None:
            raise HostedPrivateStagingError('HOSTED_PRIVATE_STAGE_PROJECT_PUBLISHED')
        provider = getattr(self.documents, 'project_document_inventory', None)
        if not callable(provider):
            raise HostedPrivateStagingError('HOSTED_PRIVATE_STAGE_INVENTORY_UNAVAILABLE')
        try:
            rows = provider(pid, max_documents=_MAX_STAGING_DOCS,
                            max_collections=_MAX_STAGING_COLLECTIONS)
        except Exception:
            raise HostedPrivateStagingError('HOSTED_PRIVATE_STAGE_INVENTORY_UNAVAILABLE') from None
        paths = set()
        for row in rows:
            path = tuple(row.path)
            if (path in paths or not _registered_staging_path(path, pid) or
                    type(row.revision) is not int or row.revision < 1):
                raise HostedPrivateStagingError('HOSTED_PRIVATE_STAGE_UNKNOWN_DOCUMENT')
            paths.add(path)
        try:
            objects = self.objects.list_prefix(f'projects/{pid}/')
        except Exception:
            raise HostedPrivateStagingError('HOSTED_PRIVATE_STAGE_OBJECT_INVENTORY_FAILED') from None
        if len(objects) > _MAX_STAGING_OBJECTS:
            raise HostedPrivateStagingError('HOSTED_PRIVATE_STAGE_OBJECT_LIMIT')
        from hosted.domain_store import _HYBRID_PAYLOAD_FIELDS
        allowed = set(_HYBRID_PAYLOAD_FIELDS) | {'project_asset'}
        names = set()
        size = 0
        for obj in objects:
            name = getattr(obj, 'name', None)
            try:
                require_object_name(name)
            except Exception:
                raise HostedPrivateStagingError('HOSTED_PRIVATE_STAGE_OBJECT_INVALID') from None
            namespace = name.split('/') if isinstance(name, str) else []
            if (name in names or len(namespace) != 7 or
                    namespace[:4] != ['projects', pid, 'private', 'v1'] or
                    namespace[4] not in allowed or not name.endswith('.bme') or
                    obj.content_type != 'application/vnd.breadmaker.encrypted+json' or
                    obj.metadata.get('breadmaker-private-format') != FORMAT or
                    type(obj.size) is not int or obj.size < 1 or
                    type(obj.generation) is not int or obj.generation < 1):
                raise HostedPrivateStagingError('HOSTED_PRIVATE_STAGE_OBJECT_INVALID')
            names.add(name)
            size += obj.size
        # Legacy staging receipts have no trustworthy creation clock. They
        # remain ineligible until separately proved by an operator; never infer
        # their age from object generations or mutable project metadata.
        started = fields.get('staging_started_at')
        eligible = False
        if started is not None:
            created = _utc(started)
            current = now.astimezone(timezone.utc)
            if created > current:
                raise HostedPrivateStagingError('HOSTED_PRIVATE_STAGE_CLOCK_INVALID')
            eligible = current - created >= timedelta(days=retention_days)
        # Recheck both before returning. Races during the listing can invalidate
        # the candidate even without a committed receipt update.
        current_receipt = self.documents.get(('hosted_private_restore_operations', op))
        if (current_receipt is None or current_receipt.revision != receipt.revision or
                self.documents.get(('projects', pid)) is not None):
            raise HostedPrivateStagingError('HOSTED_PRIVATE_STAGE_CHANGED_DURING_SCAN')
        return HostedPrivateStagingPlan(pid, op, len(paths), len(names), size,
                                        started, eligible)
