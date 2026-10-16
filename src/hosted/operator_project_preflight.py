from __future__ import annotations

"""M9 production-facing, read-only project-deletion admission diagnostics.

No network credentials or flags can turn this module into an eraser. This
boundary explicitly requires Google adapters, rechecks inventories and the
independent archive, and returns diagnostic evidence only. Cross-provider
write-fencing and IAM must be proven in live staging before a future worker.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from hosted.firestore.contracts import DocumentConflict
from hosted.firestore.google_driver import GoogleFirestoreDocumentStore
from hosted.firestore.ids import require_project_id, require_user_id
from hosted.purge_discovery import GoogleProjectTreeDiscovery
from hosted.purge_planning import HostedPurgePlanner
from hosted.purge_production_gate import HostedProductionPurgeGate
from hosted.storage.google_driver import GoogleCloudObjectStore


@dataclass(frozen=True)
class ProductionPurgePreflight:
    fingerprint: str
    firestore_document_count: int
    orphan_parent_count: int
    content_object_count: int
    retained_archive_count: int
    archive_sha256: str
    blockers: tuple[str, ...]
    production_deletion_authorized: bool = False

    def public(self) -> dict:
        return {
            'fingerprint': self.fingerprint,
            'firestore_document_count': self.firestore_document_count,
            'orphan_parent_count': self.orphan_parent_count,
            'content_object_count': self.content_object_count,
            'retained_archive_count': self.retained_archive_count,
            'archive_verified': bool(self.archive_sha256),
            'blockers': list(self.blockers),
            'production_deletion_authorized': False,
        }


class HostedGooglePurgePreflight:
    """Accepts *only* exact Google adapter types and independent bucket names.

    A twice-matching inventory is not a transactionally frozen Firestore+GCS
    snapshot. Its fingerprints must never serve as deletion authorization.
    """

    def __init__(self, projects, recovery_service, *, retention_days: int = 30,
                 now=None, max_entries: int = 25000) -> None:
        docs, live = projects.documents, projects.objects
        archives = recovery_service.recovery_objects
        if (type(docs) is not GoogleFirestoreDocumentStore
                or type(live) is not GoogleCloudObjectStore
                or type(archives) is not GoogleCloudObjectStore):
            raise ValueError('HOSTED_OPERATOR_GOOGLE_ADAPTERS_REQUIRED')
        live_name = getattr(live._bucket, 'name', None)
        archive_name = getattr(archives._bucket, 'name', None)
        if not live_name or not archive_name or live_name == archive_name:
            raise ValueError('HOSTED_OPERATOR_INDEPENDENT_BUCKETS_REQUIRED')
        if recovery_service.projects is not projects:
            raise ValueError('HOSTED_OPERATOR_RECOVERY_PROJECT_MISMATCH')
        self.recovery = recovery_service
        self.planner = HostedPurgePlanner(
            projects, archives, retention_days=retention_days, now=now,
            max_entries=max_entries, discovery=GoogleProjectTreeDiscovery(docs),
        )

    def inspect(self, project_id: str, owner_user_id: str, *, archive_operation: str) -> ProductionPurgePreflight:
        pid, uid = require_project_id(project_id), require_user_id(owner_user_id)
        # All reads are bounded by planner/archive limits and scope.
        first = self.planner.preview(pid, uid)
        if not first.document_inventory_exhaustive or not first.retention_elapsed:
            raise DocumentConflict('HOSTED_OPERATOR_DISCOVERY_OR_RETENTION_INCOMPLETE')
        snapshot_id, name = self.recovery._key(pid, uid, archive_operation)
        meta = self.recovery.recovery_objects.stat(name)
        if meta is None:
            raise DocumentConflict('HOSTED_OPERATOR_INDEPENDENT_ARCHIVE_MISSING')
        verified = self.recovery._verify(project_id=pid, user_id=uid, snapshot_id=snapshot_id, meta=meta)
        if verified.project_generation > first.project_generation:
            raise DocumentConflict('HOSTED_OPERATOR_ARCHIVE_FROM_FUTURE')
        try:
            created = datetime.fromisoformat(verified.created_at.replace('Z', '+00:00'))
            deleted = datetime.fromisoformat(first.deleted_at.replace('Z', '+00:00'))
        except (AttributeError, ValueError) as exc:
            raise DocumentConflict('HOSTED_OPERATOR_ARCHIVE_TIMESTAMP_INVALID') from exc
        if (created.tzinfo is None or deleted.tzinfo is None
                or created.astimezone(timezone.utc) > deleted.astimezone(timezone.utc)):
            raise DocumentConflict('HOSTED_OPERATOR_PREDELETE_ARCHIVE_REQUIRED')
        # Re-read after streaming the entire archive. This does not establish a
        # cross-provider lock; concurrent A->B->A changes remain a live blocker.
        second = self.planner.preview(pid, uid)
        if first.fingerprint != second.fingerprint or first != second:
            raise DocumentConflict('HOSTED_OPERATOR_CROSS_PROVIDER_DRIFT')
        meta2 = self.recovery.recovery_objects.stat(name)
        if (meta2 is None or meta2.generation != meta.generation
                or meta2.sha256 != meta.sha256):
            raise DocumentConflict('HOSTED_OPERATOR_ARCHIVE_CHANGED')
        fingerprint = hashlib.sha256(json.dumps(
            (first.fingerprint, verified.sha256, verified.object_generation),
            separators=(',', ':')).encode()).hexdigest()
        # This is an inventory preflight, never a permit. All required live
        # safety proofs remain separate from diagnostics.
        missing = HostedProductionPurgeGate.assess().missing_proofs
        return ProductionPurgePreflight(
            fingerprint, len(first.firestore_paths), len(first.missing_parent_paths),
            len(first.content_objects), len(first.retained_recovery_objects),
            verified.sha256, missing,
        )
