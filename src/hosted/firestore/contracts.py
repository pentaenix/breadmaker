from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Protocol, TypeVar


class HostedStoreError(RuntimeError):
    """Base class for deterministic hosted persistence failures."""


class DocumentConflict(HostedStoreError):
    pass


class DocumentAlreadyExists(HostedStoreError):
    pass


class DocumentNotFound(HostedStoreError):
    pass


class ProjectUnavailable(HostedStoreError):
    """Enumeration-resistant external project lookup failure."""


class HostedAuthorizationError(HostedStoreError):
    pass


class HostedValidationError(HostedStoreError):
    pass


@dataclass(frozen=True)
class ModelContract:
    logical_entity: str
    path_template: str
    authorization_policy: str
    concurrency_policy: str
    hosted_store: str


@dataclass(frozen=True)
class StoredDocument:
    path: tuple[str, ...]
    data: Mapping[str, Any]
    revision: int


def bounded_document_rows(rows: Iterable[StoredDocument], *, max_records: int,
                          max_bytes: int) -> list[StoredDocument] | None:
    """Consume only a bounded serialized snapshot; never return partial results.

    Providers must stream rather than pre-materialize rows. One provider document
    may cross the budget, but is never retained in the returned snapshot.
    """
    if max_records < 1 or max_bytes < 1:
        raise ValueError("HOSTED_BOUNDED_QUERY_LIMIT_INVALID")
    result = []
    size = 0
    for row in rows:
        if len(result) >= max_records:
            return None
        size += len(json.dumps(dict(row.data), ensure_ascii=False,
                               allow_nan=False, default=str).encode('utf-8'))
        if size > max_bytes:
            return None
        result.append(row)
    return result


class Transaction(Protocol):
    def get(self, path: tuple[str, ...]) -> StoredDocument | None: ...
    def get_many(self, paths: list[tuple[str, ...]]) -> list[StoredDocument | None]: ...
    def query_collection(
        self, collection_path: tuple[str, ...], *, field: str | None = None, value: Any = None
    ) -> list[StoredDocument]: ...
    def create(self, path: tuple[str, ...], data: Mapping[str, Any]) -> None: ...
    def set(self, path: tuple[str, ...], data: Mapping[str, Any]) -> None: ...
    def delete(self, path: tuple[str, ...]) -> None: ...


_T = TypeVar("_T")


class DocumentStore(Protocol):
    """Minimal server-only document primitive required by hosted repositories.

    The normal release verifier uses the deterministic in-memory implementation.
    The Google implementation lives in ``google_driver.py`` and is imported only
    by hosted composition.
    """

    def get(self, path: tuple[str, ...]) -> StoredDocument | None: ...
    def query_collection(
        self,
        collection_path: tuple[str, ...],
        *,
        field: str | None = None,
        value: Any = None,
    ) -> list[StoredDocument]: ...
    def query_collection_group(
        self,
        collection_id: str,
        *,
        field: str,
        value: Any,
    ) -> list[StoredDocument]: ...
    def run_transaction(self, fn: Callable[[Transaction], _T], *, max_attempts: int = 5) -> _T: ...


class Clock(Protocol):
    def now_iso(self) -> str: ...


def require_document_path(path: Iterable[str]) -> tuple[str, ...]:
    parts = tuple(str(part) for part in path)
    if not parts or len(parts) % 2:
        raise HostedValidationError("HOSTED_DOCUMENT_PATH_INVALID")
    for part in parts:
        if not part or part in {".", ".."} or "/" in part or "\\" in part or len(part) > 512:
            raise HostedValidationError("HOSTED_DOCUMENT_PATH_COMPONENT_INVALID")
    return parts


def require_collection_path(path: Iterable[str]) -> tuple[str, ...]:
    parts = tuple(str(part) for part in path)
    if not parts or len(parts) % 2 == 0:
        raise HostedValidationError("HOSTED_COLLECTION_PATH_INVALID")
    for part in parts:
        if not part or part in {".", ".."} or "/" in part or "\\" in part or len(part) > 512:
            raise HostedValidationError("HOSTED_COLLECTION_PATH_COMPONENT_INVALID")
    return parts
