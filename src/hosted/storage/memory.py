from __future__ import annotations

import hashlib
from dataclasses import dataclass
from threading import RLock
from typing import Iterable, Mapping

from .contracts import (
    ObjectAlreadyExists,
    ObjectGenerationConflict,
    ObjectLimitExceeded,
    ObjectMetadata,
    ObjectNotFound,
    ObjectStore,
)
from .keys import require_object_name


@dataclass
class _ObjectRow:
    payload: bytes
    metadata: ObjectMetadata


class MemoryObjectStore(ObjectStore):
    """Deterministic fake with Cloud Storage generation-match semantics."""

    def __init__(self) -> None:
        self._rows: dict[str, _ObjectRow] = {}
        self._next_generation = 1
        self._lock = RLock()

    @staticmethod
    def _bounded_payload(chunks: Iterable[bytes], max_bytes: int) -> bytes:
        limit = int(max_bytes)
        if limit < 0:
            raise ValueError("HOSTED_OBJECT_LIMIT_INVALID")
        pieces: list[bytes] = []
        total = 0
        for chunk in chunks:
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise TypeError("HOSTED_OBJECT_CHUNK_NOT_BYTES")
            data = bytes(chunk)
            total += len(data)
            if total > limit:
                raise ObjectLimitExceeded("HOSTED_OBJECT_LIMIT_EXCEEDED")
            pieces.append(data)
        return b"".join(pieces)

    def stat(self, name: str) -> ObjectMetadata | None:
        key = require_object_name(name)
        with self._lock:
            row = self._rows.get(key)
            return row.metadata if row is not None else None

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
        expected = int(if_generation_match)
        if expected < 0:
            raise ValueError("HOSTED_OBJECT_GENERATION_INVALID")
        payload = self._bounded_payload(chunks, max_bytes)
        digest = hashlib.sha256(payload).hexdigest()
        custom = {str(k): str(v) for k, v in dict(metadata or {}).items()}
        with self._lock:
            existing = self._rows.get(key)
            if expected == 0:
                if existing is not None:
                    raise ObjectAlreadyExists("HOSTED_OBJECT_ALREADY_EXISTS")
            elif existing is None or existing.metadata.generation != expected:
                raise ObjectGenerationConflict("HOSTED_OBJECT_GENERATION_CONFLICT")
            generation = self._next_generation
            self._next_generation += 1
            result = ObjectMetadata(
                name=key,
                generation=generation,
                size=len(payload),
                sha256=digest,
                content_type=str(content_type or "application/octet-stream"),
                metadata=custom,
            )
            self._rows[key] = _ObjectRow(payload=payload, metadata=result)
            return result

    def read_chunks(
        self,
        name: str,
        *,
        max_bytes: int,
        if_generation_match: int | None = None,
        chunk_size: int = 256 * 1024,
    ) -> Iterable[bytes]:
        key = require_object_name(name)
        limit = int(max_bytes)
        size = int(chunk_size)
        if limit < 0 or size < 1:
            raise ValueError("HOSTED_OBJECT_READ_LIMIT_INVALID")
        with self._lock:
            row = self._rows.get(key)
            if row is None:
                raise ObjectNotFound("HOSTED_OBJECT_NOT_FOUND")
            if if_generation_match is not None and row.metadata.generation != int(if_generation_match):
                raise ObjectGenerationConflict("HOSTED_OBJECT_GENERATION_CONFLICT")
            if row.metadata.size > limit:
                raise ObjectLimitExceeded("HOSTED_OBJECT_LIMIT_EXCEEDED")
            payload = bytes(row.payload)
        return tuple(payload[offset : offset + size] for offset in range(0, len(payload), size))

    def delete(self, name: str, *, if_generation_match: int) -> None:
        key = require_object_name(name)
        expected = int(if_generation_match)
        if expected < 1:
            raise ValueError("HOSTED_OBJECT_DELETE_GENERATION_REQUIRED")
        with self._lock:
            row = self._rows.get(key)
            if row is None:
                raise ObjectNotFound("HOSTED_OBJECT_NOT_FOUND")
            if row.metadata.generation != expected:
                raise ObjectGenerationConflict("HOSTED_OBJECT_GENERATION_CONFLICT")
            del self._rows[key]

    def list_prefix(self, prefix: str) -> list[ObjectMetadata]:
        value = str(prefix or "")
        if value.startswith("/") or "\\" in value or ".." in value.split("/"):
            raise ValueError("HOSTED_OBJECT_PREFIX_INVALID")
        with self._lock:
            return [self._rows[name].metadata for name in sorted(self._rows) if name.startswith(value)]
