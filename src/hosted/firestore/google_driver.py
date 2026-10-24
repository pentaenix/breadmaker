from __future__ import annotations

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


class _GoogleTransaction(Transaction):
    def __init__(self, client: Any, transaction: Any) -> None:
        self._client = client
        self._transaction = transaction

    def _ref(self, path: tuple[str, ...]):
        return self._client.document("/".join(require_document_path(path)))

    def get(self, path: tuple[str, ...]) -> StoredDocument | None:
        ref = self._ref(path)
        snapshot = ref.get(transaction=self._transaction)
        if not snapshot.exists:
            return None
        return StoredDocument(tuple(path), snapshot.to_dict() or {}, _snapshot_revision(snapshot))

    def query_collection(
        self, collection_path: tuple[str, ...], *, field: str | None = None, value: Any = None
    ) -> list[StoredDocument]:
        key = require_collection_path(collection_path)
        query = self._client.collection("/".join(key))
        if field is not None:
            try:
                from google.cloud.firestore_v1.base_query import FieldFilter
                query = query.where(filter=FieldFilter(str(field), "==", value))
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("HOSTED_FIRESTORE_SDK_REQUIRED") from exc
        snapshots = query.stream(transaction=self._transaction)
        return [StoredDocument(tuple(doc.reference.path.split("/")), doc.to_dict() or {}, _snapshot_revision(doc)) for doc in snapshots]

    def get_many(self, paths: list[tuple[str, ...]]) -> list[StoredDocument | None]:
        keys=[require_document_path(path) for path in paths]
        # Firestore BatchGet results are unordered and may include missing rows.
        # Keep caller order and use the SAME transaction, before any writes.
        snapshots=self._client.get_all([self._ref(path) for path in keys],transaction=self._transaction)
        found={tuple(doc.reference.path.split('/')):StoredDocument(
            tuple(doc.reference.path.split('/')),doc.to_dict() or {},_snapshot_revision(doc))
            for doc in snapshots if doc.exists}
        return [found.get(path) for path in keys]

    def count_collection(self, collection_path: tuple[str, ...]) -> int:
        key = require_collection_path(collection_path)
        results = self._client.collection('/'.join(key)).count(alias='total').get(transaction=self._transaction)
        return int(results[0][0].value)

    def create(self, path: tuple[str, ...], data: Mapping[str, Any]) -> None:
        self._transaction.create(self._ref(path), dict(data))

    def set(self, path: tuple[str, ...], data: Mapping[str, Any]) -> None:
        self._transaction.set(self._ref(path), dict(data))

    def delete(self, path: tuple[str, ...]) -> None:
        self._transaction.delete(self._ref(path))


def _snapshot_revision(snapshot: Any) -> int:
    stamp = getattr(snapshot, "update_time", None) or getattr(snapshot, "create_time", None)
    if stamp is None:
        return 0
    seconds = int(getattr(stamp, "timestamp", lambda: 0.0)() * 1_000_000)
    return seconds


class GoogleFirestoreDocumentStore(DocumentStore):
    """Thin Google SDK boundary used only by hosted runtime composition.

    The import is intentionally lazy so local Breadmaker and deterministic release
    tests never need Application Default Credentials or the Google client library.
    """

    def __init__(self, *, project: str | None = None, database: str | None = None, client: Any = None) -> None:
        if client is not None:
            self._client = client
            self._firestore = None
            return
        try:
            from google.cloud import firestore
        except ImportError as exc:  # pragma: no cover - exercised only by hosted image
            raise RuntimeError("HOSTED_FIRESTORE_SDK_REQUIRED") from exc
        kwargs: dict[str, Any] = {}
        if project:
            kwargs["project"] = str(project)
        if database:
            kwargs["database"] = str(database)
        self._client = firestore.Client(**kwargs)
        self._firestore = firestore

    def get(self, path: tuple[str, ...]) -> StoredDocument | None:
        key = require_document_path(path)
        snapshot = self._client.document("/".join(key)).get()
        if not snapshot.exists:
            return None
        return StoredDocument(key, snapshot.to_dict() or {}, _snapshot_revision(snapshot))

    def query_collection(
        self,
        collection_path: tuple[str, ...],
        *,
        field: str | None = None,
        value: Any = None,
    ) -> list[StoredDocument]:
        key = require_collection_path(collection_path)
        query = self._client.collection("/".join(key))
        if field is not None:
            try:
                from google.cloud.firestore_v1.base_query import FieldFilter
                query = query.where(filter=FieldFilter(str(field), "==", value))
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("HOSTED_FIRESTORE_SDK_REQUIRED") from exc
        return [StoredDocument(tuple(doc.reference.path.split("/")), doc.to_dict() or {}, _snapshot_revision(doc)) for doc in query.stream()]

    def query_collection_group(
        self,
        collection_id: str,
        *,
        field: str,
        value: Any,
    ) -> list[StoredDocument]:
        try:
            from google.cloud.firestore_v1.base_query import FieldFilter
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("HOSTED_FIRESTORE_SDK_REQUIRED") from exc
        query = self._client.collection_group(str(collection_id)).where(filter=FieldFilter(str(field), "==", value))
        return [StoredDocument(tuple(doc.reference.path.split("/")), doc.to_dict() or {}, _snapshot_revision(doc)) for doc in query.stream()]

    def project_document_inventory(self, project_id: str, *, max_documents: int = 12000,
                                   max_collections: int = 1800) -> tuple[StoredDocument, ...]:
        """Bounded recursive Firestore subtree walk, never a collection-group query.

        This independently discovers unknown *reachable* subcollections using
        the provider document-reference API. Firestore may hide descendants of
        missing parent documents; absence of those physical orphans cannot be
        proven by this API and requires M13 provider-level inventory evidence.
        """
        from .ids import require_project_id
        prefix = ('projects', require_project_id(project_id))
        rows: list[StoredDocument] = []
        seen: set[tuple[str, ...]] = set()
        collections = 0

        def walk(reference, *, root: bool = False) -> None:
            nonlocal collections
            path = tuple(reference.path.split('/'))
            if path[:2] != prefix or len(path) % 2 or path in seen:
                raise ValueError('HOSTED_RECOVERY_PHYSICAL_PATH_INVALID')
            seen.add(path)
            if len(seen) > max_documents:
                raise ValueError('HOSTED_RECOVERY_PHYSICAL_ROW_LIMIT')
            snapshot = reference.get()
            if not snapshot.exists:
                # A missing ancestor cannot safely be ignored; its descendants
                # might survive without a visible parent record.
                raise ValueError('HOSTED_RECOVERY_PHYSICAL_ORPHAN_PARENT')
            rows.append(StoredDocument(path, snapshot.to_dict() or {}, _snapshot_revision(snapshot)))
            for collection in reference.collections():
                collection_path = tuple(collection.path.split('/'))
                if (collection_path[:2] != prefix or collection_path[:-1] != path or
                        len(collection_path) % 2 == 0):
                    raise ValueError('HOSTED_RECOVERY_PHYSICAL_PATH_INVALID')
                collections += 1
                if collections > max_collections:
                    raise ValueError('HOSTED_RECOVERY_PHYSICAL_COLLECTION_LIMIT')
                for child in collection.list_documents():
                    walk(child)

        walk(self._client.document('/'.join(prefix)), root=True)
        return tuple(rows)

    def run_transaction(self, fn: Callable[[Transaction], _T], *, max_attempts: int = 5) -> _T:
        if max_attempts < 1:
            raise ValueError("HOSTED_TRANSACTION_ATTEMPTS_INVALID")
        try:
            from google.api_core.exceptions import AlreadyExists
            from google.cloud import firestore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("HOSTED_FIRESTORE_SDK_REQUIRED") from exc
        transaction = self._client.transaction(max_attempts=max_attempts)

        @firestore.transactional
        def execute(txn):
            return fn(_GoogleTransaction(self._client, txn))

        try:
            return execute(transaction)
        except AlreadyExists as exc:
            raise DocumentAlreadyExists("HOSTED_DOCUMENT_ALREADY_EXISTS") from exc
