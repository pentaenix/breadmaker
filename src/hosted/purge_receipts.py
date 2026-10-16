from __future__ import annotations

"""M9C2 preparation receipts: durable and replayable, NEVER destructive.

These records prove that an owner-approved, fully enumerated project snapshot
was observed alongside a verified independent backup. They are NOT a license to
delete data, and deliberately do not advance any deletion cursor. A later
milestone must add a separate, gated deletion executor and recovery drills.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from hosted.firestore.contracts import DocumentConflict, ProjectUnavailable
from hosted.firestore.ids import require_project_id, require_user_id
from hosted.purge_planning import HostedPurgePlanner
from hosted.recovery_archives import HostedRecoveryArchiveService

_KEY = re.compile(r'^[A-Za-z0-9_-]{1,160}$')


@dataclass(frozen=True)
class PurgePreparation:
    project_id: str
    project_generation: int
    fingerprint: str
    archive_name: str
    archive_generation: int
    archive_sha256: str
    receipt_id: str
    state: str = 'prepared_read_only'
    resume_cursor: int = 0
    physical_purge_authorized: bool = False

    def public(self) -> dict[str, Any]:
        return {
            'project_id': self.project_id,
            'state': self.state,
            'project_generation': self.project_generation,
            'fingerprint': self.fingerprint,
            'archive_sha256': self.archive_sha256,
            'resume_cursor': 0,
            'physical_purge_authorized': False,
        }


class HostedPurgePreparationService:
    """Fail-closed owner-only read-only receipt, stable across process restarts.

    Each project has one global preparation slot outside the project document
    tree. Receipt creation binds the idempotency key, project generation, tombstone
    instant, independent archive identity, and exact inventory fingerprint.
    A second key cannot silently replace a prepared operation. A retry rechecks
    live authorization and inventory before it returns the original receipt.
    """

    def __init__(self, planner: HostedPurgePlanner) -> None:
        if planner.discovery is None:
            raise ValueError('HOSTED_PURGE_EXHAUSTIVE_DISCOVERY_REQUIRED')
        self.planner = planner
        self.documents = planner.documents
        self.recovery = HostedRecoveryArchiveService(planner.projects, planner.recovery_objects)

    def _verified_backup(self, pid: str, uid: str, required_generation: int):
        # This is the pre-tombstone generation: deleting a project advances its
        # root generation exactly once, and no post-delete edits are legal.
        matching = []
        for meta in self.planner.recovery_objects.list_prefix(f'recovery/v1/projects/{pid}/'):
            details = dict(meta.metadata)
            if details.get('breadmaker-project-generation') != str(required_generation):
                continue
            if details.get('breadmaker-owner-id') != uid:
                continue
            record = self.recovery._verify(
                project_id=pid, user_id=uid,
                snapshot_id=str(details.get('breadmaker-snapshot-id') or ''), meta=meta,
            )
            matching.append(record)
        if not matching:
            raise DocumentConflict('HOSTED_PURGE_VERIFIED_INDEPENDENT_BACKUP_REQUIRED')
        # Deterministic selection; exact object generation and hash are frozen
        # into receipt. No attempt is made to delete/expire the backup.
        return sorted(matching, key=lambda record: (record.snapshot_id, record.object_generation))[0]

    def prepare(self, project_id: str, owner_user_id: str, *, operation_key: str) -> PurgePreparation:
        pid, uid = require_project_id(project_id), require_user_id(owner_user_id)
        if not isinstance(operation_key, str) or not _KEY.fullmatch(operation_key):
            raise ValueError('HOSTED_PURGE_OPERATION_KEY_INVALID')
        plan = self.planner.preview(pid, uid)
        if not plan.retention_elapsed:
            raise DocumentConflict('HOSTED_PURGE_RETENTION_NOT_ELAPSED')
        if not plan.document_inventory_exhaustive:
            raise DocumentConflict('HOSTED_PURGE_DISCOVERY_INCOMPLETE')
        backup = self._verified_backup(pid, uid, plan.project_generation - 1)
        operation_hash = hashlib.sha256(f'{pid}\0{uid}\0{operation_key}'.encode('utf-8')).hexdigest()
        path = ('purge_preparations', pid)
        prepared = {
            'project_id': pid, 'owner_user_id': uid,
            'project_generation': plan.project_generation,
            'deleted_at': plan.deleted_at,
            'fingerprint': plan.fingerprint,
            'archive_name': backup.object_name,
            'archive_generation': backup.object_generation,
            'archive_sha256': backup.sha256,
            'receipt_id': operation_hash,
            'state': 'prepared_read_only',
            'resume_cursor': 0,
            'physical_purge_authorized': False,
        }

        def reserve(tx):
            project = tx.get(('projects', pid))
            owner = tx.get(('projects', pid, 'members', uid))
            if project is None or owner is None or owner.data.get('role') != 'owner':
                raise ProjectUnavailable('PROJECT_NOT_FOUND')
            if (project.data.get('status') != 'deleted'
                    or project.data.get('deleted_at') != plan.deleted_at
                    or int(project.data.get('generation') or 0) != plan.project_generation):
                raise DocumentConflict('HOSTED_PURGE_CONCURRENT_MUTATION')
            existing = tx.get(path)
            if existing is not None:
                if dict(existing.data) != prepared:
                    raise DocumentConflict('HOSTED_PURGE_RECEIPT_CONFLICT')
                return dict(existing.data)
            tx.create(path, prepared)
            return prepared

        data = self.documents.run_transaction(reserve)
        # This object is intentionally not a command/authorization: deletion
        # is not available even with an otherwise valid preparation receipt.
        return PurgePreparation(
            project_id=pid, project_generation=int(data['project_generation']),
            fingerprint=str(data['fingerprint']),
            archive_name=str(data['archive_name']),
            archive_generation=int(data['archive_generation']),
            archive_sha256=str(data['archive_sha256']),
            receipt_id=str(data['receipt_id']),
        )

    def resume_preview(self, project_id: str, owner_user_id: str, *, operation_key: str) -> PurgePreparation:
        """Restart-safe revalidation; never mutates a deletion cursor."""
        return self.prepare(project_id, owner_user_id, operation_key=operation_key)
