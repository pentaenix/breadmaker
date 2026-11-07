from __future__ import annotations

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


class Transaction(Protocol):
    def get(self, path: tuple[str, ...]) -> StoredDocument | None: ...
    def get_many(self, paths: list[tuple[str, ...]]) -> list[StoredDocument | None]: ...
    def query_collection(
        self, collection_path: tuple[str, ...], *, field: str | None = None, value: Any = None
    ) -> list[StoredDocument]: ...
    def query_collection_in(
        self, collection_path: tuple[str, ...], *, field: str, values: list[str] | tuple[str, ...]
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


MAX_MEMBERSHIP_QUERY_VALUES = 30


def require_string_membership_query(field, values) -> tuple[str, ...]:
    """One bounded Firestore IN predicate, limited to nonempty string values.

    Keep this separate from equality queries: callers must explicitly opt into
    the provider's disjunction bound rather than silently fetching everything.
    """
    if (not isinstance(field, str) or not field
            or not isinstance(values, (list, tuple))
            or not 1 <= len(values) <= MAX_MEMBERSHIP_QUERY_VALUES
            or any(not isinstance(value, str) or not value for value in values)):
        raise HostedValidationError("HOSTED_MEMBERSHIP_QUERY_INVALID")
    return tuple(dict.fromkeys(values))


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
