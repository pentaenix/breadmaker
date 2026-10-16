from __future__ import annotations

"""M9C2: isolated administrative Firestore tree discovery (never a delete API).

Unlike the product repository's registered-collection query, this boundary walks
*every* collection beneath a project, including children of missing parent docs.
Callers cannot treat a successful scan as a transactionally frozen snapshot:
state changes between Firestore and GCS still require a separate write fence.
"""

from dataclasses import dataclass
from typing import Protocol

from hosted.firestore.contracts import DocumentConflict, require_document_path
from hosted.firestore.ids import require_project_id


@dataclass(frozen=True)
class ProjectTree:
    documents: tuple[tuple[str, ...], ...]
    missing_parents: tuple[tuple[str, ...], ...]
    complete: bool
    document_states: tuple[tuple[tuple[str, ...], str], ...] = ()


class ProjectTreeDiscovery(Protocol):
    def scan(self, project_id: str, *, max_entries: int) -> ProjectTree: ...


def _scope(root: tuple[str, ...], path: tuple[str, ...]) -> None:
    require_document_path(path)
    if len(path) < 2 or path[:2] != root:
        raise DocumentConflict('HOSTED_PURGE_DISCOVERY_SCOPE_VIOLATION')


class MemoryProjectTreeDiscovery:
    """Exact, one-lock traversal of in-memory documents; test-only provider.

    Does not mutate or extend the frozen memory DocumentStore adapter.
    Synthetic ancestors of orphaned descendants are explicitly identified.
    """

    def __init__(self, documents) -> None:
        self.documents = documents
        if not hasattr(documents, '_lock') or not hasattr(documents, '_documents'):
            raise ValueError('HOSTED_PURGE_DISCOVERY_MEMORY_DRIVER_REQUIRED')

    def scan(self, project_id: str, *, max_entries: int) -> ProjectTree:
        root = ('projects', require_project_id(project_id))
        if not 1 <= int(max_entries) <= 25_000:
            raise ValueError('HOSTED_PURGE_DISCOVERY_BUDGET_INVALID')
        with self.documents._lock:
            states = tuple(sorted((path, str(row[1])) for path, row in self.documents._documents.items()
                                  if path[:2] == root))
        paths = tuple(path for path, _ in states)
        missing = set()
        path_set = set(paths)
        for path in paths:
            _scope(root, path)
            for n in range(2, len(path), 2):
                parent = path[:n]
                if parent not in path_set:
                    missing.add(parent)
                if len(paths) + len(missing) > max_entries:
                    raise ValueError('HOSTED_PURGE_INVENTORY_TOO_LARGE')
        if len(paths) + len(missing) > max_entries:
            raise ValueError('HOSTED_PURGE_INVENTORY_TOO_LARGE')
        return ProjectTree(paths, tuple(sorted(missing)), True, states)


class GoogleProjectTreeDiscovery:
    """Explicit show_missing=True traversal using Firestore administrative RPCs.

    Firestore's ordinary collection queries omit nonexistent parents containing
    nested collections. `list_documents(show_missing=True)` includes these so
    their children can be traversed; no field-level records are downloaded.
    A missing SDK/private RPC capability or an incomplete page fails closed.
    This class is NOT used in the ordinary hosted request/composition path.
    """

    def __init__(self, document_store) -> None:
        self.client = getattr(document_store, '_client', None)
        self.api = getattr(self.client, '_firestore_api', None)
        if self.client is None or self.api is None or not callable(getattr(self.api, 'list_documents', None)):
            raise ValueError('HOSTED_PURGE_DISCOVERY_SHOW_MISSING_REQUIRED')

    def scan(self, project_id: str, *, max_entries: int) -> ProjectTree:
        root = ('projects', require_project_id(project_id))
        if not 1 <= int(max_entries) <= 25_000:
            raise ValueError('HOSTED_PURGE_DISCOVERY_BUDGET_INVALID')
        root_ref = self.client.document('/'.join(root))
        seen: dict[tuple[str, ...], str | None] = {}
        stack = [root_ref]
        while stack:
            parent = stack.pop()
            parent_path = tuple(parent.path.split('/'))
            _scope(root, parent_path)
            # .collections() lists all child collection IDs even on a missing
            # document; unlike registered model queries, it is not schema-bound.
            for collection in parent.collections():
                if not str(collection.path).startswith('/'.join(root) + '/'):
                    raise DocumentConflict('HOSTED_PURGE_DISCOVERY_SCOPE_VIOLATION')
                # The Firestore v1 RPC's show_missing flag is REQUIRED here.
                # Iteration exhausts all RPC pages or raises a provider error.
                request = {
                    'parent': parent._document_path,
                    'collection_id': collection.id,
                    'show_missing': True,
                    'page_size': 250,
                    'mask': {'field_paths': []},
                }
                for document in self.api.list_documents(request=request):
                    name = str(document.name)
                    if '/documents/' not in name:
                        raise DocumentConflict('HOSTED_PURGE_DISCOVERY_PATH_INVALID')
                    path = tuple(name.split('/documents/', 1)[1].split('/'))
                    _scope(root, path)
                    if len(path) != len(parent_path) + 2 or path[:-2] != parent_path or path[-2] != collection.id:
                        raise DocumentConflict('HOSTED_PURGE_DISCOVERY_SCOPE_VIOLATION')
                    if path in seen:
                        raise DocumentConflict('HOSTED_PURGE_DISCOVERY_DUPLICATE_PATH')
                    # Missing document proto has no create/update timestamps.
                    stamp = getattr(document, 'update_time', None) or getattr(document, 'create_time', None)
                    seen[path] = str(stamp) if stamp else None
                    if len(seen) > max_entries:
                        raise ValueError('HOSTED_PURGE_INVENTORY_TOO_LARGE')
                    stack.append(self.client.document('/'.join(path)))
        # The root exists because the planner separately validates its owner
        # and tombstone in Firestore. It must appear in the deletion inventory.
        root_snapshot = root_ref.get()
        if not root_snapshot.exists or not getattr(root_snapshot, 'update_time', None):
            raise DocumentConflict('HOSTED_PURGE_DISCOVERY_ROOT_MISSING')
        root_state = str(root_snapshot.update_time)
        documents = (root,) + tuple(sorted(path for path, stamp in seen.items() if stamp))
        missing = tuple(sorted(path for path, stamp in seen.items() if not stamp))
        if len(documents) + len(missing) > max_entries:
            raise ValueError('HOSTED_PURGE_INVENTORY_TOO_LARGE')
        states = ((root, root_state),) + tuple(sorted((path, stamp) for path, stamp in seen.items() if stamp))
        return ProjectTree(documents, missing, True, states)
