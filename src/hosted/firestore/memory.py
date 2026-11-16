from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Any, Callable, Mapping, TypeVar

from .contracts import (
    DocumentAlreadyExists,
    DocumentStore,
    StoredDocument,
    Transaction,
    require_collection_path,
    require_document_path,
)

_T = TypeVar("_T")


class _MemoryTransaction(Transaction):
    def __init__(self, owner: "MemoryDocumentStore") -> None:
        self._owner = owner

    def get(self, path: tuple[str, ...]) -> StoredDocument | None:
        return self._owner._get_locked(require_document_path(path))

    def get_many(self, paths: list[tuple[str, ...]]) -> list[StoredDocument | None]:
        return [self.get(path) for path in paths]

    def query_collection(
        self, collection_path: tuple[str, ...], *, field: str | None = None, value: Any = None
    ) -> list[StoredDocument]:
        prefix = require_collection_path(collection_path)
        depth = len(prefix) + 1
        rows = [
            self._owner._get_locked(path)
            for path in sorted(self._owner._documents)
            if len(path) == depth and path[: len(prefix)] == prefix
        ]
        out = [row for row in rows if row is not None]
        if field is not None:
            out = [row for row in out if row.data.get(field) == value]
        return out

    def saved_word_project(self, project_id: str, *, timeout: float):
        from .ids import require_project_id
        if not 0 < timeout <= 30:
            raise ValueError('WORD_METADATA_QUERY_LIMIT')
        row = self.get(('projects', require_project_id(project_id)))
        return (StoredDocument(row.path, {key: value for key, value in row.data.items()
                    if key in {'status', 'generation'}}, row.revision) if row else None)

    def saved_word_metadata(self, project_id: str, *, limit: int, timeout: float):
        from .ids import require_project_id
        if type(limit) is not int or not 1 <= limit <= 1001 or not 0 < timeout <= 30:
            raise ValueError('WORD_METADATA_QUERY_LIMIT')
        rows = self.query_collection(('projects', require_project_id(project_id), 'blocks'))[:limit]
        return [StoredDocument(row.path, {key: value for key, value in row.data.items()
                    if key in {'generation', '_bm_saved_word_v1'}}, row.revision) for row in rows]

    def saved_word_owners(self, project_id: str, *, timeout: float):
        from .ids import require_project_id
        if not 0 < timeout <= 2:
            raise ValueError('WORD_OWNER_QUERY_LIMIT')
        rows = self.query_collection(('projects', require_project_id(project_id), 'members'),
                                     field='role', value='owner')[:2]
        return [StoredDocument(row.path, {k: row.data[k] for k in ('user_id', 'role', 'created_at')
                    if k in row.data}, row.revision) for row in rows]

    def saved_word_memberships(self, user_id: str, *, timeout: float):
        from .ids import require_user_id
        uid = require_user_id(user_id)
        if not 0 < timeout <= 2: raise ValueError('WORD_MEMBERSHIP_QUERY_LIMIT')
        rows = self._owner.query_collection_group('members', field='user_id', value=uid)[:101]
        return [StoredDocument(row.path, {k: row.data[k] for k in ('user_id', 'role', 'created_at')
                    if k in row.data}, row.revision) for row in rows]

    def saved_word_page(self, project_id: str, *, after_id=None, limit=33, timeout=2):
        from .ids import require_project_id, require_opaque_id
        if after_id is not None: require_opaque_id(after_id, label='block_id')
        if type(limit) is not int or not 1 <= limit <= 33 or not 0 < timeout <= 2:
            raise ValueError('WORD_PAGE_QUERY_LIMIT')
        rows = self.query_collection(('projects', require_project_id(project_id), 'blocks'))
        rows = [row for row in rows if after_id is None or row.path[-1] > after_id][:limit]
        return [StoredDocument(row.path, {k: row.data[k] for k in ('generation', '_bm_saved_word_v1')
                    if k in row.data}, row.revision) for row in rows]

    def count_collection(self, collection_path: tuple[str, ...]) -> int:
        prefix = require_collection_path(collection_path)
        return sum(len(path) == len(prefix)+1 and path[:-1] == prefix for path in self._owner._documents)

    def create(self, path: tuple[str, ...], data: Mapping[str, Any]) -> None:
        key = require_document_path(path)
        if key in self._owner._documents:
            raise DocumentAlreadyExists("HOSTED_DOCUMENT_ALREADY_EXISTS")
        self._owner._write_locked(key, data)

    def set(self, path: tuple[str, ...], data: Mapping[str, Any]) -> None:
        self._owner._write_locked(require_document_path(path), data)

    def delete(self, path: tuple[str, ...]) -> None:
        key = require_document_path(path)
        self._owner._documents.pop(key, None)


class MemoryDocumentStore(DocumentStore):
    """Thread-safe deterministic Firestore-semantic test driver.

    A transaction owns one process lock for its complete callback. This deliberately
    favors determinism in release tests: concurrent writers are serialized and the
    repository's logical generation CAS decides which stale writer fails.
    """

    def __init__(self, *, retry_once: bool = False) -> None:
        self._documents: dict[tuple[str, ...], tuple[dict[str, Any], int]] = {}
        self._next_revision = 1
        self._lock = RLock()
        self._retry_once = bool(retry_once)

    def _write_locked(self, path: tuple[str, ...], data: Mapping[str, Any]) -> None:
        revision = self._next_revision
        self._next_revision += 1
        self._documents[path] = (deepcopy(dict(data)), revision)

    def _get_locked(self, path: tuple[str, ...]) -> StoredDocument | None:
        row = self._documents.get(path)
        if row is None:
            return None
        data, revision = row
        return StoredDocument(path, deepcopy(data), revision)

    def project_document_inventory(self, project_id: str, *, max_documents: int = 12000,
                                   max_collections: int = 1800) -> tuple[StoredDocument, ...]:
        """Independent physical subtree inventory, including orphan ancestors.

        Used only by the explicitly authorized encrypted recovery path. Unlike
        collection-by-model traversal, this sees arbitrary unknown nested paths
        and children whose Firestore parent document was deleted.
        """
        from .ids import require_project_id
        prefix = ('projects', require_project_id(project_id))
        with self._lock:
            paths = tuple(sorted(path for path in self._documents if path[:2] == prefix))
            if len(paths) > max_documents:
                raise ValueError('HOSTED_RECOVERY_PHYSICAL_ROW_LIMIT')
            collections = {path[:-1] for path in paths}
            if len(collections) > max_collections:
                raise ValueError('HOSTED_RECOVERY_PHYSICAL_COLLECTION_LIMIT')
            return tuple(self._get_locked(path) for path in paths)

    def retry_next_transaction(self) -> None:
        """Make the next transaction callback execute once on discarded shadow state.

        This is a deterministic release-test primitive for Firestore's documented
        transaction callback retry behavior; it never contacts a cloud service.
        """
        with self._lock:
            self._retry_once = True

    def get(self, path: tuple[str, ...]) -> StoredDocument | None:
        key = require_document_path(path)
        with self._lock:
            return self._get_locked(key)

    def query_collection(
        self,
        collection_path: tuple[str, ...],
        *,
        field: str | None = None,
        value: Any = None,
    ) -> list[StoredDocument]:
        prefix = require_collection_path(collection_path)
        depth = len(prefix) + 1
        with self._lock:
            rows = [
                self._get_locked(path)
                for path in sorted(self._documents)
                if len(path) == depth and path[: len(prefix)] == prefix
            ]
        out = [row for row in rows if row is not None]
        if field is not None:
            out = [row for row in out if row.data.get(field) == value]
        return out

    def query_collection_group(
        self,
        collection_id: str,
        *,
        field: str,
        value: Any,
    ) -> list[StoredDocument]:
        collection_id = str(collection_id)
        if not collection_id or "/" in collection_id or "\\" in collection_id:
            raise ValueError("HOSTED_COLLECTION_GROUP_INVALID")
        with self._lock:
            rows = [
                self._get_locked(path)
                for path in sorted(self._documents)
                if len(path) >= 2 and path[-2] == collection_id
            ]
        return [row for row in rows if row is not None and row.data.get(field) == value]

    def run_transaction(self, fn: Callable[[Transaction], _T], *, max_attempts: int = 5) -> _T:
        # max_attempts is accepted to mirror the cloud driver contract. The memory
        # driver cannot experience provider retryable-abort errors because one lock
        # makes the callback atomic.
        if max_attempts < 1:
            raise ValueError("HOSTED_TRANSACTION_ATTEMPTS_INVALID")
        with self._lock:
            if self._retry_once:
                # Simulate a provider transaction callback that completed locally but
                # was aborted before commit, then retried. The shadow writes are
                # discarded; repository callbacks must therefore have no external
                # side effects and must be safe to invoke again.
                shadow = MemoryDocumentStore()
                shadow._documents = deepcopy(self._documents)
                shadow._next_revision = self._next_revision
                fn(_MemoryTransaction(shadow))
                self._retry_once = False
            # Match provider atomicity on callback failure too. A process lock
            # serializes writers but does not roll back earlier callback writes.
            before, revision = deepcopy(self._documents), self._next_revision
            try:
                return fn(_MemoryTransaction(self))
            except BaseException:
                self._documents, self._next_revision = before, revision
                raise
