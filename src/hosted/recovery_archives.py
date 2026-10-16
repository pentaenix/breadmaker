from __future__ import annotations

"""M9A: immutable, independently discoverable hosted project recovery archives.

This service must be given a *different* ObjectStore from the live content store.
The independent GCS bucket/IAM/retention resources and job wiring belong to
M10/M9D respectively; merely instantiating this service cannot create them.

Snapshots are self-contained hosted project packages; they are NOT yet local
Schema-42 import packages (M9B owns that translation/round-trip contract).
"""

import hashlib
import re
import secrets
from dataclasses import dataclass
from typing import Any

from hosted.firestore.contracts import DocumentConflict, DocumentStore, HostedValidationError
from hosted.firestore.ids import require_project_id, require_user_id
from hosted.firestore.repositories import HostedProjectRepository
from hosted.packages import HostedProjectPackageService
from hosted.storage.contracts import ObjectIntegrityError, ObjectLimitExceeded, ObjectMetadata, ObjectNotFound, ObjectStore
from hosted.storage.keys import require_object_name
from hosted.storage.publication import ImmutableObjectPublisher

# Synchronous M9A operations never make a container keep arbitrary-sized data.
# M9D may introduce separate bounded Cloud Run Jobs for larger projects.
MAX_RECOVERY_ARCHIVE_BYTES = 64 * 1024 * 1024
_OPERATION_RE = re.compile(r'^[A-Za-z0-9_-]{1,160}$')
_ARCHIVE_CONTENT_TYPE = 'application/vnd.breadmaker.project+zip'


@dataclass(frozen=True)
class RecoveryArchive:
    project_id: str
    project_generation: int
    snapshot_id: str
    object_name: str
    object_generation: int
    sha256: str
    size: int
    created_at: str

    def public(self) -> dict[str, Any]:
        return {
            'project_id': self.project_id,
            'project_generation': self.project_generation,
            'snapshot_id': self.snapshot_id,
            'sha256': self.sha256,
            'size': self.size,
            'created_at': self.created_at,
        }


class HostedRecoveryArchiveService:
    """Owner-only archive creation/read against independent immutable object storage.

    Firestore is used only for live owner authorization and a generation fence.
    No Firestore document/index/receipt is required to *discover* the backup:
    an authorized disaster recovery operator can enumerate the recovery bucket
    using ``recovery/v1/projects/<project_id>/`` after Firestore is unavailable.
    The service intentionally has NO delete method.
    """

    def __init__(self, projects, recovery_objects: ObjectStore, *, max_bytes: int = MAX_RECOVERY_ARCHIVE_BYTES) -> None:
        self.projects = projects
        self.documents: DocumentStore = projects.documents
        self.authority = HostedProjectRepository(self.documents)
        self.recovery_objects = recovery_objects
        # Runtime is not allowed to nominate its live content bucket as a backup.
        if recovery_objects is projects.objects:
            raise ValueError('HOSTED_RECOVERY_INDEPENDENT_BUCKET_REQUIRED')
        live_bucket = str(getattr(getattr(projects.objects, '_bucket', None), 'name', '') or '')
        recovery_bucket = str(getattr(getattr(recovery_objects, '_bucket', None), 'name', '') or '')
        if live_bucket and recovery_bucket and live_bucket == recovery_bucket:
            raise ValueError('HOSTED_RECOVERY_INDEPENDENT_BUCKET_REQUIRED')
        self.maximum = int(max_bytes)
        if not 1 <= self.maximum <= MAX_RECOVERY_ARCHIVE_BYTES:
            raise ValueError('HOSTED_RECOVERY_BUDGET_INVALID')
        self.publisher = ImmutableObjectPublisher(recovery_objects)

    def _owner_generation(self, project_id: str, user_id: str) -> int:
        pid, uid = require_project_id(project_id), require_user_id(user_id)

        def read(tx):
            project, _member = self.authority._require_member(tx, pid, uid, owner=True)
            generation = int(project.get('generation') or 0)
            if generation < 1:
                raise DocumentConflict('HOSTED_RECOVERY_GENERATION_INVALID')
            return generation

        return self.documents.run_transaction(read)

    @staticmethod
    def _key(project_id: str, user_id: str, operation: str) -> tuple[str, str]:
        pid, uid = require_project_id(project_id), require_user_id(user_id)
        if not _OPERATION_RE.fullmatch(operation):
            raise HostedValidationError('HOSTED_RECOVERY_OPERATION_INVALID')
        # A request key can be retried on any instance without creating a second
        # archive. Distinct owner IDs cannot share a backup operation namespace.
        fingerprint = hashlib.sha256(f'{uid}\0{pid}\0{operation}'.encode('utf-8')).hexdigest()
        snapshot_id = 'snap_' + fingerprint
        name = require_object_name(f'recovery/v1/projects/{pid}/{snapshot_id}.breadmaker')
        return snapshot_id, name

    def _verify(self, *, project_id: str, user_id: str, snapshot_id: str, meta: ObjectMetadata) -> RecoveryArchive:
        details = dict(meta.metadata)
        if (
            details.get('breadmaker-recovery-format') != 'hosted-v3'
            or details.get('breadmaker-project-id') != project_id
            or details.get('breadmaker-owner-id') != user_id
            or details.get('breadmaker-snapshot-id') != snapshot_id
        ):
            raise ObjectIntegrityError('HOSTED_RECOVERY_METADATA_MISMATCH')
        try:
            generation = int(details['breadmaker-project-generation'])
        except (KeyError, ValueError, TypeError) as exc:
            raise ObjectIntegrityError('HOSTED_RECOVERY_GENERATION_MISSING') from exc
        if generation < 1 or meta.generation < 1 or meta.size < 1 or meta.size > self.maximum:
            raise ObjectIntegrityError('HOSTED_RECOVERY_OBJECT_BOUNDS_INVALID')
        expected = str(details.get('breadmaker-sha256') or '')
        if len(expected) != 64 or any(ch not in '0123456789abcdef' for ch in expected):
            raise ObjectIntegrityError('HOSTED_RECOVERY_SHA256_MISSING')
        digest = hashlib.sha256()
        total = 0
        for chunk in self.recovery_objects.read_chunks(
            meta.name, max_bytes=self.maximum, if_generation_match=meta.generation,
        ):
            total += len(chunk)
            digest.update(chunk)
        if total != meta.size or digest.hexdigest() != expected:
            raise ObjectIntegrityError('HOSTED_RECOVERY_OBJECT_HASH_MISMATCH')
        return RecoveryArchive(
            project_id=project_id, project_generation=generation, snapshot_id=snapshot_id,
            object_name=meta.name, object_generation=meta.generation, sha256=expected,
            size=total, created_at=str(details.get('breadmaker-created-at') or ''),
        )

    def create(self, project_id: str, user_id: str, *, operation: str | None = None) -> RecoveryArchive:
        pid, uid = require_project_id(project_id), require_user_id(user_id)
        snapshot_id, name = self._key(pid, uid, operation or 'manual_' + secrets.token_hex(16))
        before = self._owner_generation(pid, uid)
        existing = self.recovery_objects.stat(name)
        if existing is not None:
            # Replay returns the original frozen snapshot even after later edits.
            return self._verify(project_id=pid, user_id=uid, snapshot_id=snapshot_id, meta=existing)
        payload = HostedProjectPackageService(self.projects).export_bytes(pid, uid)
        if not payload or len(payload) > self.maximum:
            raise ObjectLimitExceeded('HOSTED_RECOVERY_ARCHIVE_TOO_LARGE')
        after = self._owner_generation(pid, uid)
        if before != after:
            # Project child mutations must advance the same project generation.
            # Never promote a torn Firestore/GCS traversal into a recovery copy.
            raise DocumentConflict('HOSTED_RECOVERY_CONCURRENT_MUTATION')
        digest = hashlib.sha256(payload).hexdigest()
        published = self.publisher.publish_bytes(
            name, payload, max_bytes=self.maximum, content_type=_ARCHIVE_CONTENT_TYPE,
            metadata={
                'breadmaker-recovery-format': 'hosted-v3',
                'breadmaker-project-id': pid,
                'breadmaker-project-generation': str(before),
                'breadmaker-owner-id': uid,
                'breadmaker-snapshot-id': snapshot_id,
                'breadmaker-created-at': self.authority.clock.now_iso(),
            },
        )
        if published.object.sha256 != digest:
            raise ObjectIntegrityError('HOSTED_RECOVERY_OBJECT_HASH_MISMATCH')
        return self._verify(project_id=pid, user_id=uid, snapshot_id=snapshot_id, meta=published.object)

    def read(self, project_id: str, user_id: str, *, operation: str) -> bytes:
        pid, uid = require_project_id(project_id), require_user_id(user_id)
        snapshot_id, name = self._key(pid, uid, operation)
        self._owner_generation(pid, uid)
        meta = self.recovery_objects.stat(name)
        if meta is None:
            raise ObjectNotFound('HOSTED_RECOVERY_ARCHIVE_NOT_FOUND')
        record = self._verify(project_id=pid, user_id=uid, snapshot_id=snapshot_id, meta=meta)
        data = b''.join(self.recovery_objects.read_chunks(
            name, max_bytes=self.maximum, if_generation_match=record.object_generation,
        ))
        if len(data) != record.size or hashlib.sha256(data).hexdigest() != record.sha256:
            raise ObjectIntegrityError('HOSTED_RECOVERY_OBJECT_HASH_MISMATCH')
        return data
