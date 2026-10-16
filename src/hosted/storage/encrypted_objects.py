from __future__ import annotations

"""M12C: opt-in, authenticated immutable private-object publication boundary.

This intentionally does NOT replace the legacy content-addressed ObjectStore:
legacy callers assert plaintext SHA-based names/pointers. Migrating them without
reworking idempotency, Firestore models and recovery would create bypasses.

No local runtime imports this module. Caller supplies an authorization callback
backed by canonical hosted membership and must invoke this service only after
its authenticated request has been bound to the correct logical record.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Callable, Mapping

from hosted.firestore.ids import require_opaque_id, require_project_id, require_user_id
from hosted.security.envelope import (
    FORMAT, MAX_ENVELOPE_BYTES, MAX_PAYLOAD, EnvelopeError, HostedEnvelopeCodec,
)
from hosted.storage.contracts import (
    ObjectAlreadyExists, ObjectGenerationConflict, ObjectIntegrityError,
    ObjectLimitExceeded, ObjectNotFound, ObjectStore,
)
from hosted.storage.keys import require_object_name, require_sha256

_CONTENT_TYPE = 'application/vnd.breadmaker.encrypted+json'
_MAX = MAX_ENVELOPE_BYTES


@dataclass(frozen=True)
class EncryptedObjectReference:
    """Only ciphertext properties; NEVER plaintext-derived hashes or titles."""

    name: str
    generation: int
    ciphertext_sha256: str
    ciphertext_size: int


class HostedEncryptedObjectStore:
    """Real generation-conditional ObjectStore operations over ciphertext.

    16 MiB plaintext budget; larger archives and assets require separately
    authenticated streaming chunks. No plaintext fallback or delete API.

    ``authorize`` MUST be a callable with (project_id, user_id) inputs that
    rejects unauthorized membership. This primitive authorizes project scope;
    record-level authorization remains the caller's explicit responsibility.
    """

    def __init__(
        self,
        objects: ObjectStore,
        codec: HostedEnvelopeCodec,
        *,
        authorize: Callable[[str, str], object],
    ) -> None:
        if not callable(authorize):
            raise ValueError('HOSTED_ENCRYPTED_AUTHORIZER_REQUIRED')
        self._objects = objects
        self._codec = codec
        self._authorize = authorize

    @staticmethod
    def object_name(project_id: str, entity: str, record_id: str, operation_id: str) -> str:
        pid = require_project_id(project_id)
        entity_id = require_opaque_id(entity, label='entity')
        record = require_opaque_id(record_id, label='record_id')
        operation = require_opaque_id(operation_id, label='operation_id')
        # Names describe server-owned logical identifiers, never content hashes,
        # manuscript titles or plaintext content types.
        return require_object_name(f'projects/{pid}/private/v1/{entity_id}/{record}/{operation}.bme')

    def _scope(self, project_id: str, entity: str, record_id: str, operation_id: str,
               user_id: str) -> tuple[str, dict[str, str]]:
        name = self.object_name(project_id, entity, record_id, operation_id)
        pid = require_project_id(project_id)
        uid = require_user_id(user_id)
        # Authorization precedes every SDK/object access or KMS operation.
        permitted = self._authorize(pid, uid)
        # Require affirmative membership proof.  A callback that forgets to
        # return, or returns an empty/false-like lookup, must not expose GCS/KMS.
        # Canonical membership lookups may return a nonempty project document.
        if not permitted:
            raise PermissionError('HOSTED_ENCRYPTED_NOT_AUTHORIZED')
        # Hash the FULL canonical name to bind every path component and operation
        # to authenticated encryption despite the v1 AAD's 160-byte record limit.
        context = {'project_id': pid, 'entity': require_opaque_id(entity, label='entity'),
                   'record_id': hashlib.sha256(name.encode('utf-8')).hexdigest()}
        return name, context

    def _verified_bytes(self, *, name: str, generation: int, size: int, digest: str) -> bytes:
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
            raise ObjectIntegrityError('HOSTED_ENCRYPTED_GENERATION_INVALID')
        if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= _MAX:
            raise ObjectIntegrityError('HOSTED_ENCRYPTED_SIZE_INVALID')
        expected = require_sha256(digest)
        observed = self._objects.stat(name)
        if observed is None:
            raise ObjectNotFound('HOSTED_ENCRYPTED_OBJECT_NOT_FOUND')
        if observed.generation != generation or observed.size != size:
            raise ObjectIntegrityError('HOSTED_ENCRYPTED_OBJECT_CHANGED')
        if (observed.content_type != _CONTENT_TYPE or
                observed.metadata.get('breadmaker-private-format') != FORMAT):
            raise ObjectIntegrityError('HOSTED_ENCRYPTED_METADATA_INVALID')
        total = 0
        hash_ = hashlib.sha256()
        pieces: list[bytes] = []
        for part in self._objects.read_chunks(name, max_bytes=_MAX,
                                               if_generation_match=generation):
            total += len(part)
            if total > size:
                raise ObjectIntegrityError('HOSTED_ENCRYPTED_SIZE_MISMATCH')
            hash_.update(part)
            pieces.append(bytes(part))
        if total != size or hash_.hexdigest() != expected:
            raise ObjectIntegrityError('HOSTED_ENCRYPTED_CIPHERTEXT_MISMATCH')
        return b''.join(pieces)

    def _open(self, data: bytes, *, context: Mapping[str, str]) -> bytes:
        try:
            envelope = json.loads(data)
        except (UnicodeError, ValueError):
            raise ObjectIntegrityError('HOSTED_ENCRYPTED_FORMAT_INVALID') from None
        if not isinstance(envelope, dict):
            raise ObjectIntegrityError('HOSTED_ENCRYPTED_FORMAT_INVALID')
        try:
            return self._codec.open(envelope, context=context)
        except EnvelopeError:
            raise ObjectIntegrityError('HOSTED_ENCRYPTED_AUTHENTICATION_FAILED') from None

    def publish(self, *, project_id: str, user_id: str, entity: str,
                record_id: str, operation_id: str, plaintext: bytes) -> EncryptedObjectReference:
        name, context = self._scope(project_id, entity, record_id, operation_id, user_id)
        if not isinstance(plaintext, bytes) or len(plaintext) > MAX_PAYLOAD:
            raise ObjectLimitExceeded('HOSTED_ENCRYPTED_PAYLOAD_LIMIT')
        # A replay with a different payload must never silently reuse an
        # existing object. Decrypt + compare after checking generation/hash.
        present = self._objects.stat(name)
        if present is not None:
            ref = EncryptedObjectReference(name, present.generation,
                                           present.sha256, present.size)
            if self._read_scoped(ref, name=name, context=context) != plaintext:
                raise ObjectIntegrityError('HOSTED_ENCRYPTED_OPERATION_REUSED')
            return ref
        envelope = self._codec.seal(plaintext, context=context)
        data = json.dumps(envelope, sort_keys=True, separators=(',', ':')).encode('utf-8')
        if len(data) > _MAX:
            raise ObjectLimitExceeded('HOSTED_ENCRYPTED_ENVELOPE_LIMIT')
        try:
            stored = self._objects.write_chunks(
                name, (data,), max_bytes=_MAX, content_type=_CONTENT_TYPE,
                if_generation_match=0, metadata={'breadmaker-private-format': FORMAT},
            )
        except ObjectAlreadyExists:
            # Raced identical retries converge only after authenticated readback;
            # mismatched payloads fail rather than overwriting or adopting.
            stored = self._objects.stat(name)
            if stored is None:
                raise ObjectGenerationConflict('HOSTED_ENCRYPTED_RETRY_RACE')
        digest = hashlib.sha256(data).hexdigest() if stored is not None else ''
        # In a winning race the candidate ciphertext is intentionally different.
        # Read the actual stored generation and compute its ciphertext checksum.
        if stored is None:
            raise ObjectIntegrityError('HOSTED_ENCRYPTED_OBJECT_MISSING')
        if stored.sha256 != digest:
            digest = require_sha256(stored.sha256)
        ref = EncryptedObjectReference(name, stored.generation, digest, stored.size)
        if self._read_scoped(ref, name=name, context=context) != plaintext:
            raise ObjectIntegrityError('HOSTED_ENCRYPTED_OPERATION_REUSED')
        return ref

    def _read_scoped(self, reference: EncryptedObjectReference, *, name: str,
                     context: Mapping[str, str]) -> bytes:
        data = self._verified_bytes(name=name, generation=reference.generation,
                                    size=reference.ciphertext_size,
                                    digest=reference.ciphertext_sha256)
        return self._open(data, context=context)

    def read(self, *, project_id: str, user_id: str, entity: str, record_id: str,
             operation_id: str, reference: EncryptedObjectReference) -> bytes:
        name, context = self._scope(project_id, entity, record_id, operation_id, user_id)
        if not isinstance(reference, EncryptedObjectReference) or reference.name != name:
            raise ObjectIntegrityError('HOSTED_ENCRYPTED_REFERENCE_SCOPE_MISMATCH')
        return self._read_scoped(reference, name=name, context=context)
