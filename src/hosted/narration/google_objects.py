"""Narrow bounded provider for ONLY the regenerable Narration bucket.

No browser resumable sessions, unbounded SDK retries, canonical/recovery path,
private filesystem staging, or direct delete (GC installs durable empty fences).
Normal app publication can only CREATE nonempty ciphertext. Positive-generation
replacement is restricted to exact empty GC markers, never another recording.
"""
import hashlib
import re

from hosted.narration.gc import FENCE_METADATA, is_fence
from hosted.storage.contracts import (ObjectAlreadyExists, ObjectGenerationConflict,
    ObjectIntegrityError, ObjectLimitExceeded, ObjectNotFound)
from hosted.storage.google_driver import GoogleCloudObjectStore
from hosted.narration.io_limits import io_timeout

MAX_BYTES = 2 * 1024**2 + 16
_NAME = re.compile(r'projects/[0-9a-f]{32}/narration/[0-9a-f]{32}/[0-9]{3}\.bin')


class GoogleNarrationObjects(GoogleCloudObjectStore):
    def __init__(self, *, bucket, project, client=None):
        if not isinstance(project, str) or not re.fullmatch('[a-z][a-z0-9-]{4,28}[a-z0-9]', project) or bucket != project + '-breadmaker-narration':
            raise ValueError('NARRATION_BUCKET_SCOPE_INVALID')
        super().__init__(bucket=bucket, project=project, client=client)

    @staticmethod
    def _name(name):
        if not isinstance(name, str) or not _NAME.fullmatch(name) or int(name[-7:-4]) > 15:
            raise ValueError('NARRATION_OBJECT_SCOPE_INVALID')
        return name

    def verify_gc_bucket(self):
        self._bucket.reload(timeout=io_timeout(), retry=None)
        if (self._bucket.versioning_enabled or self._bucket.retention_period
                or getattr(self._bucket.soft_delete_policy, 'retention_duration_seconds', None) != 0
                or list(self._bucket.lifecycle_rules)
                or self._bucket.iam_configuration.public_access_prevention != 'enforced'
                or not self._bucket.iam_configuration.uniform_bucket_level_access_enabled):
            raise ValueError('NARRATION_GC_BUCKET_PROTECTIONS_CHANGED')

    def stat(self, name):
        blob = self._bucket.blob(self._name(name))
        try:
            blob.reload(timeout=io_timeout(), retry=None)
        except Exception as exc:
            translated = self._translate(exc)
            if isinstance(translated, ObjectNotFound):
                return None
            raise translated from None
        return self._metadata(blob)

    def write_chunks(self, name, chunks, *, max_bytes, content_type, if_generation_match, metadata=None):
        name = self._name(name)
        if (type(max_bytes) is not int or not 0 <= max_bytes <= MAX_BYTES
                or type(if_generation_match) is not int or if_generation_match < 0
                or content_type != 'application/octet-stream'):
            raise ValueError('NARRATION_OBJECT_WRITE_INVALID')
        pieces, size = [], 0
        for chunk in chunks:
            if not isinstance(chunk, bytes):
                raise ValueError('NARRATION_OBJECT_WRITE_INVALID')
            size += len(chunk)
            if size > max_bytes:
                raise ObjectLimitExceeded('NARRATION_OBJECT_SIZE_LIMIT')
            pieces.append(chunk)
        payload = b''.join(pieces)
        fence = size == 0 and metadata == FENCE_METADATA
        if not fence and (size < 17 or metadata or if_generation_match != 0):
            raise ValueError('NARRATION_OBJECT_REPLACEMENT_FORBIDDEN')
        blob = self._bucket.blob(name)
        digest = hashlib.sha256(payload).hexdigest()
        blob.metadata = {**(metadata or {}), 'breadmaker-sha256': digest}
        try:
            # <=2 MiB uses a single multipart object insert, not a long-lived
            # resumable session. No automatic retry after an uncertain response.
            blob.upload_from_string(payload, content_type=content_type, if_generation_match=if_generation_match,
                                    checksum='auto', timeout=io_timeout(15), retry=None)
            generation = int(blob.generation or 0)
            if generation < 1:
                raise ObjectGenerationConflict('NARRATION_OBJECT_GENERATION_MISSING')
            blob.reload(if_generation_match=generation, timeout=io_timeout(), retry=None)
            result = self._metadata(blob)
            if (result.size, result.sha256, result.generation) != (size, digest, generation) or fence and not is_fence(result):
                raise ObjectIntegrityError('NARRATION_OBJECT_READBACK_FAILED')
            return result
        except Exception as exc:
            translated = self._translate(exc)
            if isinstance(translated, ObjectGenerationConflict) and if_generation_match == 0:
                raise ObjectAlreadyExists('NARRATION_OBJECT_ALREADY_EXISTS') from None
            raise translated from None

    def read_chunks(self, name, *, max_bytes, if_generation_match=None, chunk_size=256 * 1024):
        if (type(max_bytes) is not int or not 0 <= max_bytes <= MAX_BYTES
                or type(if_generation_match) is not int or if_generation_match < 1
                or type(chunk_size) is not int or not 1 <= chunk_size <= MAX_BYTES):
            raise ValueError('NARRATION_OBJECT_READ_INVALID')
        blob = self._bucket.blob(self._name(name))
        try:
            # end bounds the response BEFORE allocating it, even if a corrupt
            # reference or external writer points at a larger provider object.
            payload = blob.download_as_bytes(if_generation_match=if_generation_match, start=0, end=max_bytes,
                                             timeout=io_timeout(15), retry=None, checksum=None, raw_download=True)
        except Exception as exc:
            raise self._translate(exc) from None
        if len(payload) > max_bytes:
            raise ObjectLimitExceeded('NARRATION_OBJECT_SIZE_LIMIT')
        return tuple(payload[i:i + chunk_size] for i in range(0, len(payload), chunk_size))

    def delete(self, name, *, if_generation_match):
        raise ValueError('NARRATION_GC_FENCES_MUST_NOT_BE_DELETED')

    def list_prefix(self, prefix):
        raise ValueError('NARRATION_BOUNDED_INVENTORY_REQUIRED')

    def inventory_page(self, *, after='', limit=100):
        if (type(limit) is not int or not 1 <= limit <= 100 or not isinstance(after, str) or len(after) > 1024):
            raise ValueError('NARRATION_INVENTORY_PAGE_INVALID')
        blobs = self._client.list_blobs(self._bucket, prefix='', start_offset=after,
            max_results=limit + 2, page_size=limit + 2, timeout=io_timeout(), retry=None)
        values = [self._metadata(blob) for blob in blobs if blob.name > after]
        return values[:limit], values[limit - 1].name if len(values) > limit else ''
