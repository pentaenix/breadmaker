"""Real pinned Firestore transaction edges, with an isolated fake RPC transport."""
from types import SimpleNamespace
import time

import pytest

from hosted.narration.io_limits import deadline
from hosted.narration.maintenance import CONTROL
from hosted.narration.maintenance_google import MaintenanceGoogleStore


class RPC:
    def __init__(self): self.calls = []
    def begin_transaction(self, **kwargs):
        self.calls.append(('begin', kwargs)); return SimpleNamespace(transaction=b'synthetic-tx')
    def commit(self, **kwargs):
        self.calls.append(('commit', kwargs)); return SimpleNamespace(write_results=[], commit_time=None)
    def rollback(self, **kwargs):
        self.calls.append(('rollback', kwargs)); return None
    def batch_get_documents(self, **kwargs):
        from google.cloud.firestore_v1.types import BatchGetDocumentsResponse
        self.calls.append(('get', kwargs))
        return iter([BatchGetDocumentsResponse(missing=kwargs['request']['documents'][0])])
    def run_query(self, **kwargs):
        from google.cloud.firestore_v1.types import RunQueryResponse
        self.calls.append(('query', kwargs)); return iter([RunQueryResponse()])


@pytest.fixture
def provider(monkeypatch):
    from google.auth.credentials import AnonymousCredentials
    monkeypatch.setattr('google.auth.default', lambda **_: (AnonymousCredentials(), 'breadmaker-prod-511902'))
    store = MaintenanceGoogleStore('breadmaker-prod-511902')
    rpc = RPC()
    store._client._firestore_api_internal = rpc
    yield store, rpc
    store._client.close()


def test_sdk_begin_read_commit_and_rollback_all_have_deadline_and_no_rpc_retries(provider):
    store, rpc = provider
    def commit(tx):
        assert tx.get((CONTROL,'synthetic')) is None
        tx.set((CONTROL,'synthetic'),dict(v=1))
    token = deadline.set(time.monotonic()+2)
    try:
        store.run_transaction(commit)
        def failed(tx): raise ValueError('synthetic')
        with pytest.raises(ValueError, match='synthetic'):
            store.run_transaction(failed)
    finally:
        deadline.reset(token)
    assert [name for name,_ in rpc.calls] == ['begin','get','commit','begin','rollback']
    assert all(0 < kw['timeout'] <= 2 and kw['retry'] is None for _,kw in rpc.calls)
    writes = rpc.calls[2][1]['request']['writes']
    assert len(writes) == 1 and writes[0].update.name.endswith('/'+CONTROL+'/synthetic')


def test_expired_deadline_prevents_even_transaction_begin(provider):
    store, rpc = provider
    token = deadline.set(time.monotonic()-1)
    try:
        with pytest.raises(TimeoutError):
            store.run_transaction(lambda tx: tx.set((CONTROL,'synthetic'),dict(v=1)))
    finally:
        deadline.reset(token)
    assert not rpc.calls


def test_current_owner_and_reference_queries_are_bounded_and_other_queries_fail(provider):
    store, rpc = provider
    def read(tx):
        assert tx.query_collection(('projects','a'*32,'members'),field='role',value='owner') == []
        assert tx.query_collection(('narration_projects_v1','a'*32,'records'),field='publication',value='b'*32) == []
        with pytest.raises(ValueError,match='QUERY_SCOPE'):
            tx.query_collection(('users',))
    store.run_transaction(read)
    calls = [kwargs for name,kwargs in rpc.calls if name == 'query']
    assert len(calls) == 2
    assert all(kwargs['request']['structured_query'].limit == 2 for kwargs in calls)
    assert all(0 < kwargs['timeout'] <= 3 and kwargs['retry'] is None for _,kwargs in rpc.calls)
