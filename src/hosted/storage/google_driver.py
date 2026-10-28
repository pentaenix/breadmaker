from __future__ import annotations

import hashlib
import tempfile
from typing import Any, Iterable, Mapping

from .contracts import (
    ObjectAlreadyExists,
    ObjectGenerationConflict,
    ObjectIntegrityError,
    ObjectLimitExceeded,
    ObjectMetadata,
    ObjectNotFound,
    ObjectStore,
)
from .keys import require_object_name

_CHUNK_BYTES = 256 * 1024


class GoogleCloudObjectStore(ObjectStore):
    """Thin Cloud Storage SDK boundary used only by future hosted composition."""

    def __init__(self, *, bucket: str, project: str | None = None, client: Any = None) -> None:
        bucket_name = str(bucket or "").strip()
        if not bucket_name:
            raise ValueError("HOSTED_STORAGE_BUCKET_REQUIRED")
        if client is None:
            try:
                from google.cloud import storage
            except ImportError as exc:  # pragma: no cover - hosted image only
                raise RuntimeError("HOSTED_STORAGE_SDK_REQUIRED") from exc
            client = storage.Client(project=str(project) if project else None)
        self._client = client
        self._bucket = client.bucket(bucket_name)

    @staticmethod
    def _translate(exc: Exception) -> Exception:
        try:
            from google.api_core.exceptions import NotFound, PreconditionFailed
            from google.cloud.storage.exceptions import DataCorruption, InvalidResponse
        except ImportError:  # pragma: no cover
            return exc
        if isinstance(exc, NotFound):
            return ObjectNotFound("HOSTED_OBJECT_NOT_FOUND")
        if isinstance(exc, PreconditionFailed):
            return ObjectGenerationConflict("HOSTED_OBJECT_GENERATION_CONFLICT")
        # BlobWriter's resumable upload does not wrap media HTTP errors in
        # api_core exceptions. Preserve create-only replay semantics on HTTP412.
        if isinstance(exc, InvalidResponse) and exc.response.status_code == 412:
            return ObjectGenerationConflict("HOSTED_OBJECT_GENERATION_CONFLICT")
        if isinstance(exc, DataCorruption):
            return ObjectIntegrityError("HOSTED_OBJECT_PROVIDER_CHECKSUM_MISMATCH")
        return exc

    @staticmethod
    def _metadata(blob: Any, *, sha256: str | None = None) -> ObjectMetadata:
        custom = {str(k): str(v) for k, v in dict(getattr(blob, "metadata", None) or {}).items()}
        digest = str(sha256 or custom.get("breadmaker-sha256") or "")
        return ObjectMetadata(
            name=str(blob.name),
            generation=int(blob.generation or 0),
            size=int(blob.size or 0),
            sha256=digest,
            content_type=str(blob.content_type or "application/octet-stream"),
            metadata=custom,
        )

    def stat(self, name: str) -> ObjectMetadata | None:
        key = require_object_name(name)
        blob = self._bucket.blob(key)
        try:
            blob.reload()
        except Exception as exc:  # Google exceptions are optional locally.
            translated = self._translate(exc)
            if isinstance(translated, ObjectNotFound):
                return None
            raise translated from exc
        return self._metadata(blob)

    def write_chunks(
        self,
        name: str,
        chunks: Iterable[bytes],
        *,
        max_bytes: int,
        content_type: str,
        if_generation_match: int,
        metadata: Mapping[str, str] | None = None,
    ) -> ObjectMetadata:
        key = require_object_name(name)
        limit = int(max_bytes)
        expected = int(if_generation_match)
        if limit < 0 or expected < 0:
            raise ValueError("HOSTED_OBJECT_WRITE_ARGUMENT_INVALID")
        blob = self._bucket.blob(key)
        custom = {str(k): str(v) for k, v in dict(metadata or {}).items()}
        digest = hashlib.sha256()
        total = 0
        # Storage Object Creator IAM does not permit patching object metadata
        # after creation.  Hash into a bounded temporary spool first, then
        # submit the checksum with the ORIGINAL conditional upload.  Spool files
        # are ephemeral and closed on every path; no durable volume is needed.
        try:
            with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as staged:
                for chunk in chunks:
                    if not isinstance(chunk, (bytes, bytearray, memoryview)):
                        raise TypeError("HOSTED_OBJECT_CHUNK_NOT_BYTES")
                    data = bytes(chunk)
                    total += len(data)
                    if total > limit:
                        raise ObjectLimitExceeded("HOSTED_OBJECT_LIMIT_EXCEEDED")
                    digest.update(data)
                    staged.write(data)
                local_sha = digest.hexdigest()
                if 'breadmaker-sha256' in custom and custom['breadmaker-sha256'] != local_sha:
                    raise ObjectIntegrityError("HOSTED_OBJECT_DECLARED_SHA_MISMATCH")
                blob.metadata = {**custom, 'breadmaker-sha256': local_sha}
                staged.seek(0)
                # The staged, bounded file is already seekable. Use the SDK's
                # managed upload/error translation, not BlobWriter.close(), which
                # leaves a failed resumable transfer open for destructor retries.
                blob.upload_from_file(
                    staged,
                    size=total,
                    content_type=str(content_type or "application/octet-stream"),
                    if_generation_match=expected,
                    checksum="auto",
                )
            # No storage.objects.update / blob.patch permission is necessary.
            if not blob.generation:
                blob.reload()
            generation = int(blob.generation or 0)
            if generation < 1:
                raise ObjectGenerationConflict("HOSTED_OBJECT_GENERATION_MISSING")
            blob.reload(if_generation_match=generation)
            result = self._metadata(blob)
            if (result.generation != generation or result.size != total or
                    result.sha256 != local_sha or
                    result.metadata.get('breadmaker-sha256') != local_sha):
                raise ObjectIntegrityError("HOSTED_OBJECT_READBACK_METADATA_MISMATCH")
            return result
        except ObjectLimitExceeded:
            # A bounded streaming writer may have begun a resumable upload. Cloud
            # Storage does not expose a committed object until close succeeds.
            raise
        except Exception as exc:
            translated = self._translate(exc)
            if isinstance(translated, ObjectGenerationConflict) and expected == 0:
                raise ObjectAlreadyExists("HOSTED_OBJECT_ALREADY_EXISTS") from exc
            raise translated from exc

    def read_verified_bytes(self, name: str, *, if_generation_match: int,
                            expected_size: int, expected_sha256: str) -> bytes:
        """One bounded media GET for a small, hash-pinned manuscript bundle.

        Range reads have no provider-wide checksum; verify the mandatory SHA-256
        from the authorized Firestore pointer ourselves. Other streaming readers
        retain their existing metadata/size/streaming contract unchanged.
        """
        key=require_object_name(name)
        size,generation=int(expected_size),int(if_generation_match)
        if (not 0<size<=2*1024*1024 or generation<1 or len(expected_sha256)!=64
                or any(c not in '0123456789abcdef' for c in expected_sha256)):
            raise ValueError('HOSTED_VERIFIED_READ_ARGUMENT_INVALID')
        try:
            # Inclusive end requests ONE excess byte, detecting a truncated
            # pointer size without ever downloading an unbounded whole object.
            data=self._bucket.blob(key).download_as_bytes(start=0,end=size,
                if_generation_match=generation,raw_download=True,checksum=None,
                timeout=20,single_shot_download=True)
        except Exception as exc:
            raise self._translate(exc) from exc
        if len(data)!=size or hashlib.sha256(data).hexdigest()!=expected_sha256:
            raise ObjectIntegrityError('HOSTED_VERIFIED_READ_MISMATCH')
        return data

    def read_chunks(
        self,
        name: str,
        *,
        max_bytes: int,
        if_generation_match: int | None = None,
        chunk_size: int = _CHUNK_BYTES,
    ) -> Iterable[bytes]:
        key = require_object_name(name)
        limit = int(max_bytes)
        size = int(chunk_size)
        if limit < 0 or size < 1:
            raise ValueError("HOSTED_OBJECT_READ_LIMIT_INVALID")
        blob = self._bucket.blob(key)
        try:
            blob.reload(if_generation_match=if_generation_match)
            if int(blob.size or 0) > limit:
                raise ObjectLimitExceeded("HOSTED_OBJECT_LIMIT_EXCEEDED")
            generation = int(blob.generation or 0)
            handle = blob.open("rb", chunk_size=size, if_generation_match=generation, raw_download=True)
        except ObjectLimitExceeded:
            raise
        except Exception as exc:
            raise self._translate(exc) from exc

        def iterate():
            total = 0
            try:
                with handle:
                    while True:
                        data = handle.read(size)
                        if not data:
                            break
                        total += len(data)
                        if total > limit:
                            raise ObjectLimitExceeded("HOSTED_OBJECT_LIMIT_EXCEEDED")
                        yield data
            except ObjectLimitExceeded:
                raise
            except Exception as exc:
                raise self._translate(exc) from exc

        return iterate()

    def delete(self, name: str, *, if_generation_match: int) -> None:
        key = require_object_name(name)
        expected = int(if_generation_match)
        if expected < 1:
            raise ValueError("HOSTED_OBJECT_DELETE_GENERATION_REQUIRED")
        try:
            self._bucket.blob(key).delete(if_generation_match=expected)
        except Exception as exc:
            raise self._translate(exc) from exc

    def list_prefix(self, prefix: str) -> list[ObjectMetadata]:
        value = str(prefix or "")
        if value.startswith("/") or "\\" in value or ".." in value.split("/"):
            raise ValueError("HOSTED_OBJECT_PREFIX_INVALID")
        out: list[ObjectMetadata] = []
        try:
            for blob in self._client.list_blobs(self._bucket, prefix=value):
                out.append(self._metadata(blob))
        except Exception as exc:
            raise self._translate(exc) from exc
        return sorted(out, key=lambda row: row.name)
