from __future__ import annotations

"""M12H: opt-in, independently restorable encrypted hosted-v3 archives.

This is a separate non-HTTP operator path. It does NOT modify the legacy
scheduled/plaintext archive job. M12I admits an explicitly dual-codec
private source into an ephemeral in-memory ZIP that is sealed immediately;
public export/import routes still fail closed. No local component imports it.

The source ZIP is split into at most eight 8-MiB chunks. Each chunk and the
commit manifest are independently KMS-wrapped AES-256-GCM objects in a separate
recovery bucket. The manifest is written last; incomplete uploads cannot be
mistaken for committed backups. Archive data hashes exist inside ciphertext,
never as plaintext-derived GCS object names or public metadata.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Callable

from hosted.firestore.contracts import DocumentConflict, HostedValidationError
from hosted.firestore.ids import require_project_id, require_user_id
from hosted.packages import HostedProjectPackageService
from hosted.recovery_archives import HostedRecoveryArchiveService, MAX_RECOVERY_ARCHIVE_BYTES
from hosted.recovery_drills import HostedRecoveryDrillService, RecoveryDrillResult
from hosted.security.envelope import HostedEnvelopeCodec
from hosted.storage.contracts import ObjectIntegrityError, ObjectLimitExceeded, ObjectNotFound, ObjectStore
from hosted.storage.encrypted_objects import EncryptedObjectReference, HostedEncryptedObjectStore

CHUNK_BYTES = 8 * 1024 * 1024
MAX_CHUNKS = 8
_FORMAT = 'breadmaker-hosted-v3-encrypted-recovery-v1'
_ALLOWED = frozenset({'format', 'project_id', 'owner_user_id', 'snapshot_id',
                      'project_generation', 'archive_bytes', 'archive_sha256', 'chunks', 'source_private'})
_REF_FIELDS = frozenset({'name', 'generation', 'ciphertext_sha256', 'ciphertext_size'})


@dataclass(frozen=True)
class EncryptedRecoveryResult:
    project_id: str
    snapshot_id: str
    project_generation: int
    manifest_name: str
    manifest_generation: int
    archive_bytes: int
    chunk_count: int


class HostedEncryptedRecoveryReader:
    """Offline reader; requires an external *positive* operator authorization.

    The callable must independently authenticate the operator's right to
    recover (project_id, owner_user_id, snapshot_id). Identifiers supplied
    by an operator request are NOT themselves proof of authorization.
    No original Firestore project or live content bucket is consulted.
    """

    def __init__(self, recovery_objects: ObjectStore, codec: HostedEnvelopeCodec, *,
                 authorize: Callable[[str, str, str], bool],
                 max_bytes: int = MAX_RECOVERY_ARCHIVE_BYTES) -> None:
        if not callable(authorize):
            raise ValueError('HOSTED_ENCRYPTED_RECOVERY_AUTH_REQUIRED')
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_RECOVERY_ARCHIVE_BYTES:
            raise ValueError('HOSTED_ENCRYPTED_RECOVERY_LIMIT_INVALID')
        self.objects = recovery_objects
        self._authorize = authorize
        self.maximum = max_bytes
        # Only used after the 3-argument operator admission gate below.
        self._encrypted = HostedEncryptedObjectStore(
            recovery_objects, codec, authorize=lambda _pid, _uid: True,
        )

    @staticmethod
    def _snapshot(project_id: str, owner_user_id: str, operation: str) -> tuple[str, str, str]:
        pid = require_project_id(project_id)
        uid = require_user_id(owner_user_id)
        if not isinstance(operation, str):
            raise HostedValidationError('HOSTED_RECOVERY_OPERATION_INVALID')
        snapshot_id, _legacy_name = HostedRecoveryArchiveService._key(pid, uid, operation)
        return pid, uid, snapshot_id

    @staticmethod
    def _identity(pid: str, uid: str, sid: str, entity: str, operation: str) -> dict[str, str]:
        return {'project_id': pid, 'user_id': uid, 'entity': entity,
                'record_id': sid, 'operation_id': operation}

    @classmethod
    def _manifest_name(cls, pid: str, sid: str) -> str:
        return HostedEncryptedObjectStore.object_name(pid, 'recovery_manifest', sid, 'commit')

    @staticmethod
    def _reference(meta) -> EncryptedObjectReference:
        return EncryptedObjectReference(meta.name, meta.generation, meta.sha256, meta.size)

    def _read_manifest(self, pid: str, uid: str, sid: str) -> tuple[dict, EncryptedObjectReference]:
        name = self._manifest_name(pid, sid)
        meta = self.objects.stat(name)
        if meta is None:
            raise ObjectNotFound('HOSTED_ENCRYPTED_RECOVERY_UNCOMMITTED')
        ref = self._reference(meta)
        blob = self._encrypted.read(
            **self._identity(pid, uid, sid, 'recovery_manifest', 'commit'), reference=ref,
        )
        if len(blob) > 16 * 1024:
            raise ObjectIntegrityError('HOSTED_ENCRYPTED_RECOVERY_MANIFEST_INVALID')
        try:
            manifest = json.loads(blob)
        except (UnicodeError, ValueError):
            raise ObjectIntegrityError('HOSTED_ENCRYPTED_RECOVERY_MANIFEST_INVALID') from None
        if not isinstance(manifest, dict) or set(manifest) != _ALLOWED:
            raise ObjectIntegrityError('HOSTED_ENCRYPTED_RECOVERY_MANIFEST_INVALID')
        if (manifest['format'] != _FORMAT or manifest['project_id'] != pid
                or manifest['owner_user_id'] != uid or manifest['snapshot_id'] != sid):
            raise ObjectIntegrityError('HOSTED_ENCRYPTED_RECOVERY_SCOPE_MISMATCH')
        if type(manifest['source_private']) is not bool:
            raise ObjectIntegrityError('HOSTED_ENCRYPTED_RECOVERY_MANIFEST_INVALID')
        gen, size, parts, digest = (manifest['project_generation'], manifest['archive_bytes'],
                                     manifest['chunks'], manifest['archive_sha256'])
        if (type(gen) is not int or gen < 1 or type(size) is not int or
                not 1 <= size <= self.maximum or not isinstance(parts, list) or
                len(parts) != (size + CHUNK_BYTES - 1) // CHUNK_BYTES or
                not 1 <= len(parts) <= MAX_CHUNKS or not isinstance(digest, str) or
                len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest)):
            raise ObjectIntegrityError('HOSTED_ENCRYPTED_RECOVERY_MANIFEST_INVALID')
        for index, part in enumerate(parts):
            if not isinstance(part, dict) or set(part) != _REF_FIELDS:
                raise ObjectIntegrityError('HOSTED_ENCRYPTED_RECOVERY_PART_INVALID')
            expected_name = HostedEncryptedObjectStore.object_name(
                pid, 'recovery_chunk', sid, f'part_{index:03d}',
            )
            if (part['name'] != expected_name or type(part['generation']) is not int or
                    part['generation'] < 1 or type(part['ciphertext_size']) is not int or
                    not 1 <= part['ciphertext_size'] <= 24 * 1024 * 1024 or
                    not isinstance(part['ciphertext_sha256'], str) or
                    len(part['ciphertext_sha256']) != 64 or
                    any(c not in '0123456789abcdef' for c in part['ciphertext_sha256'])):
                raise ObjectIntegrityError('HOSTED_ENCRYPTED_RECOVERY_PART_INVALID')
        return manifest, ref

    def read_offline(self, project_id: str, owner_user_id: str, *, operation: str) -> bytes:
        pid, uid, sid = self._snapshot(project_id, owner_user_id, operation)
        if self._authorize(pid, uid, sid) is not True:
            raise PermissionError('HOSTED_ENCRYPTED_RECOVERY_NOT_AUTHORIZED')
        manifest, _ref = self._read_manifest(pid, uid, sid)
        parts: list[bytes] = []
        total = 0
        sha = hashlib.sha256()
        for index, item in enumerate(manifest['chunks']):
            chunk = self._encrypted.read(
                **self._identity(pid, uid, sid, 'recovery_chunk', f'part_{index:03d}'),
                reference=EncryptedObjectReference(**item),
            )
            expected_size = min(CHUNK_BYTES, manifest['archive_bytes'] - total)
            if len(chunk) != expected_size:
                raise ObjectIntegrityError('HOSTED_ENCRYPTED_RECOVERY_CHUNK_SIZE_MISMATCH')
            sha.update(chunk)
            total += len(chunk)
            parts.append(chunk)
        if total != manifest['archive_bytes'] or sha.hexdigest() != manifest['archive_sha256']:
            raise ObjectIntegrityError('HOSTED_ENCRYPTED_RECOVERY_ARCHIVE_MISMATCH')
        return b''.join(parts)

    def drill_offline(self, project_id: str, owner_user_id: str, *, operation: str) -> RecoveryDrillResult:
        """Decrypted archive is restored only into fresh ephemeral memory stores."""
        pid = require_project_id(project_id)
        recovered = self.read_offline(pid, owner_user_id, operation=operation)
        return HostedRecoveryDrillService._isolated_drill(recovered, pid)


class HostedEncryptedRecoveryWriter:
    """Owner-fenced opt-in archive publisher; never routed to HTTP/scheduler.

    Reuses the existing independent-bucket and Firestore owner/generation
    checks. The plain source package is only held transiently in process RAM;
    only encrypted chunks and an encrypted manifest reach recovery GCS.
    """

    def __init__(self, projects, recovery_objects: ObjectStore, codec: HostedEnvelopeCodec,
                 *, max_bytes: int = MAX_RECOVERY_ARCHIVE_BYTES) -> None:
        self._legacy = HostedRecoveryArchiveService(projects, recovery_objects, max_bytes=max_bytes)
        self.projects = projects
        self.maximum = max_bytes
        self._encrypted = HostedEncryptedObjectStore(
            recovery_objects, codec, authorize=self._owner_authorize,
        )
        self._reader = HostedEncryptedRecoveryReader(
            recovery_objects, codec, authorize=self._operator_read_owner, max_bytes=max_bytes,
        )

    def _owner_authorize(self, pid: str, uid: str) -> bool:
        self._legacy._owner_generation(pid, uid)
        return True

    def _operator_read_owner(self, pid: str, uid: str, _sid: str) -> bool:
        self._legacy._owner_generation(pid, uid)
        return True

    def create(self, project_id: str, owner_user_id: str, *, operation: str) -> EncryptedRecoveryResult:
        pid, uid, sid = self._reader._snapshot(project_id, owner_user_id, operation)
        before = self._legacy._owner_generation(pid, uid)
        manifest_name = self._reader._manifest_name(pid, sid)
        if self._reader.objects.stat(manifest_name) is not None:
            manifest, ref = self._reader._read_manifest(pid, uid, sid)
            # Already committed operation must always remain readable.
            self._reader.read_offline(pid, uid, operation=operation)
            return EncryptedRecoveryResult(pid, sid, manifest['project_generation'],
                                           manifest_name, ref.generation,
                                           manifest['archive_bytes'], len(manifest['chunks']))

        package = HostedProjectPackageService(self.projects)
        if self.projects.domain.encrypted_fields is not None or self.projects.domain.encrypted_objects is not None:
            # M12I: the ONLY encrypted-source escape from the ordinary ZIP/JSON
            # export ban. Rehydrate via authenticated domain reads in RAM and
            # encrypt every archive chunk before publishing it. Reject partial
            # codec configuration; never fall back to a plaintext GCS archive.
            payload = package._sealed_recovery_source(pid, uid)
        else:
            # Historical unencrypted-source path is preserved; its exact
            # public export method is still guarded for private source records.
            payload = package.export_bytes(pid, uid)
        if not isinstance(payload, bytes) or not 1 <= len(payload) <= self.maximum:
            raise ObjectLimitExceeded('HOSTED_ENCRYPTED_RECOVERY_ARCHIVE_LIMIT')
        parts = []
        for index, start in enumerate(range(0, len(payload), CHUNK_BYTES)):
            ref = self._encrypted.publish(
                **self._reader._identity(pid, uid, sid, 'recovery_chunk', f'part_{index:03d}'),
                plaintext=payload[start:start + CHUNK_BYTES],
            )
            parts.append({'name': ref.name, 'generation': ref.generation,
                          'ciphertext_sha256': ref.ciphertext_sha256,
                          'ciphertext_size': ref.ciphertext_size})
        after = self._legacy._owner_generation(pid, uid)
        if after != before:
            # Previous chunks are immutable but orphaned; no committed archive.
            raise DocumentConflict('HOSTED_ENCRYPTED_RECOVERY_CONCURRENT_MUTATION')
        manifest = {
            'format': _FORMAT, 'project_id': pid, 'owner_user_id': uid,
            'snapshot_id': sid, 'project_generation': before,
            'source_private': self.projects.domain.encrypted_fields is not None and self.projects.domain.encrypted_objects is not None,
            'archive_bytes': len(payload), 'archive_sha256': hashlib.sha256(payload).hexdigest(),
            'chunks': parts,
        }
        sealed_manifest = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode('utf-8')
        ref = self._encrypted.publish(
            **self._reader._identity(pid, uid, sid, 'recovery_manifest', 'commit'),
            plaintext=sealed_manifest,
        )
        # Cryptographic readback validates manifest + all chunks before success.
        if self._reader.read_offline(pid, uid, operation=operation) != payload:
            raise ObjectIntegrityError('HOSTED_ENCRYPTED_RECOVERY_READBACK_MISMATCH')
        return EncryptedRecoveryResult(pid, sid, before, ref.name, ref.generation,
                                       len(payload), len(parts))
