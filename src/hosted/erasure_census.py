"""Read-only, point-in-time Firestore subtree census for staged erasure.

Unlike ordinary queries, ListDocuments(show_missing=True) discovers orphan
parents. EVERY RPC, including ListCollectionIds, uses the same read_time. This
does not freeze writers, discover references outside the requested roots, or
authorize deletion. No normal runtime or HTTP composition imports this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
import time

from hosted.firestore.contracts import DocumentConflict, require_document_path

_PROJECT = re.compile(r'[a-z][a-z0-9-]{4,28}[a-z0-9]')
_STAMP = re.compile(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?Z')


def exact_timestamp(value: str) -> str:
    """Canonical UTC nanoseconds; never round provider write preconditions."""
    match = _STAMP.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ValueError('ERASURE_TIMESTAMP_INVALID')
    try:
        datetime.fromisoformat(match[1] + '+00:00')
    except ValueError:
        raise ValueError('ERASURE_TIMESTAMP_INVALID') from None
    return match[1] + '.' + (match[2] or '').ljust(9, '0') + 'Z'


@dataclass(frozen=True)
class CensusDocument:
    path: tuple[str, ...]
    update_time: str


@dataclass(frozen=True)
class SnapshotCensus:
    database: str
    read_time: str
    roots: tuple[tuple[str, ...], ...]
    documents: tuple[CensusDocument, ...]
    missing_parents: tuple[tuple[str, ...], ...]
    rpc_calls: int

    def public(self) -> dict:
        return {'document_count': len(self.documents),
                'missing_parent_count': len(self.missing_parents),
                'rpc_calls': self.rpc_calls, 'scope': 'requested_subtrees_only',
                'cross_provider_frozen': False, 'deletion_authorized': False}


class GoogleSnapshotCensus:
    """Bounded metadata RPCs only, including explicit pagination budgets.

    The pinned SDK is lazy/injected, so local/offline writing needs no Google.
    Leaf documents also require collection discovery: a registry of known
    collections cannot prove that unknown descendants are absent. Large scans
    can therefore be expensive and MUST fail rather than return partial proof.
    """
    def __init__(self, client, *, project: str, read_time: str):
        if not isinstance(project, str) or not _PROJECT.fullmatch(project):
            raise ValueError('ERASURE_PROJECT_INVALID')
        self.database = f'projects/{project}/databases/(default)'
        self.prefix = self.database + '/documents/'
        if getattr(client, '_database_string', None) != self.database:
            raise ValueError('ERASURE_DATABASE_MISMATCH')
        self.api = getattr(client, '_firestore_api', None)
        if any(not callable(getattr(self.api, method, None)) for method in
               ('get_document', 'list_documents', 'list_collection_ids')):
            raise ValueError('ERASURE_SNAPSHOT_RPC_REQUIRED')
        self.read_time = exact_timestamp(read_time)
        # Firestore historical reads require microsecond precision (or whole
        # minutes for older PITR reads). Provider API enforces the age window.
        if self.read_time[-4:-1] != '000':
            raise ValueError('ERASURE_READ_TIME_PRECISION_INVALID')

    def scan(self, roots, *, max_entries: int = 25_000, max_calls: int = 12_000,
             max_seconds: int = 240, max_depth: int = 64) -> SnapshotCensus:
        for value, ceiling in ((max_entries, 25_000), (max_calls, 50_000),
                               (max_seconds, 3600), (max_depth, 100)):
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError('ERASURE_CENSUS_BUDGET_INVALID')
        scope = tuple(sorted(require_document_path(root) for root in roots))
        if not scope or len(scope) > 128 or len(set(scope)) != len(scope):
            raise ValueError('ERASURE_CENSUS_ROOTS_INVALID')
        if any(a != b and a[:len(b)] == b for a in scope for b in scope):
            raise ValueError('ERASURE_CENSUS_OVERLAPPING_ROOTS')
        if any(len(root) // 2 > max_depth for root in scope):
            raise ValueError('ERASURE_CENSUS_DEPTH_EXCEEDED')
        documents, missing, seen = {}, set(), set()
        calls, deadline = 0, time.monotonic() + max_seconds

        def rpc(method, request):
            nonlocal calls
            if calls >= max_calls or time.monotonic() >= deadline:
                raise ValueError('ERASURE_CENSUS_RPC_BUDGET_EXCEEDED')
            calls += 1
            try:
                result = getattr(self.api, method)(request=request, retry=None,
                                                  timeout=min(20, max(0.001, deadline-time.monotonic())))
                # GAPIC list RPCs return pagers. Consume ONLY their first page;
                # automatic pager iteration would escape the RPC/deadline budget.
                return next(iter(result.pages)) if hasattr(result, 'pages') else result
            except Exception:
                # Provider error text may contain account/document identifiers.
                raise DocumentConflict('ERASURE_CENSUS_PROVIDER_READ_FAILED') from None

        def pages(method, request, field):
            token, tokens = '', set()
            while True:
                response = rpc(method, {**request, 'page_token': token, 'page_size': 250})
                values = getattr(response, field)
                if len(values) > 250:
                    raise DocumentConflict('ERASURE_CENSUS_PAGE_INVALID')
                yield from values
                token = response.next_page_token
                if not token:
                    return
                if not isinstance(token, str) or len(token) > 8192 or token in tokens:
                    raise DocumentConflict('ERASURE_CENSUS_PAGE_CYCLE')
                tokens.add(token)

        def record(document, expected_parent=None, collection=None):
            name = str(document.name)
            if not name.startswith(self.prefix):
                raise DocumentConflict('ERASURE_CENSUS_FOREIGN_DATABASE')
            path = require_document_path(name[len(self.prefix):].split('/'))
            if not any(path[:len(root)] == root for root in scope):
                raise DocumentConflict('ERASURE_CENSUS_SCOPE_VIOLATION')
            if expected_parent is not None and (path[:-2] != expected_parent or path[-2] != collection):
                raise DocumentConflict('ERASURE_CENSUS_CHILD_PATH_INVALID')
            if path in seen:
                raise DocumentConflict('ERASURE_CENSUS_DUPLICATE_DOCUMENT')
            if len(path) // 2 > max_depth:
                raise ValueError('ERASURE_CENSUS_DEPTH_EXCEEDED')
            seen.add(path)
            if len(seen) > max_entries:
                raise ValueError('ERASURE_CENSUS_ENTRY_BUDGET_EXCEEDED')
            if getattr(document, 'fields', None):
                raise DocumentConflict('ERASURE_CENSUS_UNEXPECTED_BODY')
            stamp = getattr(document, 'update_time', None)
            created = getattr(document, 'create_time', None)
            if stamp:
                raw = stamp.rfc3339() if callable(getattr(stamp, 'rfc3339', None)) else stamp
                timestamp = exact_timestamp(raw)
                if timestamp > self.read_time:
                    raise DocumentConflict('ERASURE_CENSUS_FUTURE_DOCUMENT')
                documents[path] = timestamp
            elif created:
                raise DocumentConflict('ERASURE_CENSUS_UPDATE_TIME_MISSING')
            else:
                missing.add(path)
            return path

        stack = []
        for root in scope:
            # An erased/missing root is not proof that its descendants are gone.
            # ListDocuments(show_missing=True) in the parent collection finds it.
            # Avoid a GET 404 becoming a false empty-tree success.
            name = self.prefix + '/'.join(root)
            try:
                document = rpc('get_document', {'name': name, 'read_time': self.read_time,
                                               'mask': {'field_paths': []}})
            except DocumentConflict:
                # Do not reinterpret permission failures, timeouts or generic
                # provider errors as missing documents. Root absence requires a
                # parent show_missing scan by the caller, never a guessed root.
                raise
            if record(document) != root:
                raise DocumentConflict('ERASURE_CENSUS_ROOT_MISMATCH')
            stack.append(root)
        while stack:
            parent = stack.pop()
            parent_name = self.prefix + '/'.join(parent)
            collections = set()
            for collection in pages('list_collection_ids',
                    {'parent': parent_name, 'read_time': self.read_time}, 'collection_ids'):
                require_document_path(('collection', collection))
                if collection in collections:
                    raise DocumentConflict('ERASURE_CENSUS_DUPLICATE_COLLECTION')
                collections.add(collection)
                for document in pages('list_documents', {
                        'parent': parent_name, 'collection_id': collection,
                        'show_missing': True, 'mask': {'field_paths': []},
                        'read_time': self.read_time}, 'documents'):
                    stack.append(record(document, parent, collection))
        return SnapshotCensus(self.database, self.read_time, scope,
            tuple(CensusDocument(path, documents[path]) for path in sorted(documents)),
            tuple(sorted(missing)), calls)
