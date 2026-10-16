from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Protocol


class HostedObjectError(RuntimeError):
    """Base class for deterministic hosted object-storage failures."""


class ObjectAlreadyExists(HostedObjectError):
    pass


class ObjectNotFound(HostedObjectError):
    pass


class ObjectGenerationConflict(HostedObjectError):
    pass


class ObjectIntegrityError(HostedObjectError):
    pass


class ObjectLimitExceeded(HostedObjectError):
    pass


class ObjectNameInvalid(HostedObjectError):
    pass


@dataclass(frozen=True)
class ObjectMetadata:
    name: str
    generation: int
    size: int
    sha256: str
    content_type: str
    metadata: Mapping[str, str] = field(default_factory=dict)


class ObjectStore(Protocol):
    """Minimal generation-aware object primitive for hosted Breadmaker.

    Implementations must apply ``if_generation_match`` to every mutation.  The
    create-only value is zero, matching Cloud Storage ``ifGenerationMatch=0``.
    Streams are bounded by ``max_bytes`` at the adapter boundary so callers never
    need an unbounded in-memory or disposable-filesystem copy.
    """

    def stat(self, name: str) -> ObjectMetadata | None: ...

    def write_chunks(
        self,
        name: str,
        chunks: Iterable[bytes],
        *,
        max_bytes: int,
        content_type: str,
        if_generation_match: int,
        metadata: Mapping[str, str] | None = None,
    ) -> ObjectMetadata: ...

    def read_chunks(
        self,
        name: str,
        *,
        max_bytes: int,
        if_generation_match: int | None = None,
        chunk_size: int = 256 * 1024,
    ) -> Iterable[bytes]: ...

    def delete(self, name: str, *, if_generation_match: int) -> None: ...

    def list_prefix(self, prefix: str) -> list[ObjectMetadata]: ...
