"""Deadline-bound Firestore client ONLY for the scoped maintenance port.

Firestore's high-level transaction begin/commit/rollback omit caller deadlines.
This isolated Client property adapter bounds EVERY RPC, including those edges,
without changing the shared canonical provider/client or its retry contracts.
The pinned google-cloud-firestore graph and focused SDK tests cover this seam.
"""
from hosted.firestore.google_driver import GoogleFirestoreDocumentStore, _GoogleTransaction
from hosted.firestore.contracts import DocumentAlreadyExists
from hosted.narration.io_limits import io_timeout


class BoundedRPC:
    def __init__(self, api): self.api = api
    def __getattr__(self, name):
        call = getattr(self.api, name)
        def bounded(*args, **kwargs):
            return call(*args, **{**kwargs, 'timeout': io_timeout(3), 'retry': None})
        return bounded


def _client(project):
    from google.cloud.firestore_v1.client import Client
    class MaintenanceClient(Client):
        @property
        def _firestore_api(self):
            return BoundedRPC(super()._firestore_api)
    return MaintenanceClient(project=project, database='(default)')


class _Transaction(_GoogleTransaction):
    def query_collection(self, collection_path, *, field=None, value=None):
        # Both current callers need to prove EXACTLY ONE owner/reference.
        # Two results prove ambiguity; an unbounded members scan is unnecessary.
        from hosted.firestore.contracts import require_collection_path, StoredDocument
        from hosted.firestore.google_driver import _snapshot_revision
        from google.cloud.firestore_v1.base_query import FieldFilter
        path = require_collection_path(collection_path)
        if not ((len(path) == 3 and path[0] == 'projects' and path[2] == 'members' and field == 'role' and value == 'owner')
                or (len(path) == 3 and path[0] == 'narration_projects_v1' and path[2] == 'records' and field == 'publication')):
            raise ValueError('NARRATION_GC_QUERY_SCOPE_INVALID')
        query = self._client.collection('/'.join(path)).where(filter=FieldFilter(field, '==', value)).limit(2)
        return [StoredDocument(tuple(row.reference.path.split('/')), row.to_dict() or {}, _snapshot_revision(row))
                for row in query.stream(transaction=self._transaction, retry=None, timeout=io_timeout(3))]


class MaintenanceGoogleStore(GoogleFirestoreDocumentStore):
    def __init__(self, project):
        super().__init__(client=_client(project))

    def run_transaction(self, fn, *, max_attempts=5):
        io_timeout(3)  # before SDK begin/automatic rollback on a missing tx ID
        from google.api_core.exceptions import AlreadyExists
        from google.cloud import firestore
        transaction = self._client.transaction(max_attempts=min(max_attempts, 2))
        @firestore.transactional
        def execute(tx):
            io_timeout(3)  # reject retries/callback work after the overall deadline
            return fn(_Transaction(self._client, tx))
        try:
            return execute(transaction)
        except AlreadyExists:
            raise DocumentAlreadyExists('HOSTED_DOCUMENT_ALREADY_EXISTS') from None
