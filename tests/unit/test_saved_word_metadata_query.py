"""No credentials/network: enforce projection, prefetch bound and transaction use."""
from types import SimpleNamespace
from hosted.firestore.google_driver import _GoogleTransaction


def test_projected_provider_reads_are_bounded_and_same_transaction():
    calls = []
    transaction = object()
    pid = 'a' * 32
    class Reference:
        def get(self, **kwargs):
            calls.append(('root', kwargs))
            return SimpleNamespace(exists=True, to_dict=lambda: {'status': 'active', 'generation': 3})
    class Stream:
        def __iter__(self):
            return iter([])
        def close(self):
            calls.append(('closed',))
    class Query:
        def select(self, fields):
            calls.append(('select', fields)); return self
        def limit(self, limit):
            calls.append(('limit', limit)); return self
        def stream(self, **kwargs):
            calls.append(('stream', kwargs)); return Stream()
    class Client:
        def document(self, path):
            assert path == 'projects/' + pid
            return Reference()
        def collection(self, path):
            assert path == 'projects/' + pid + '/blocks'
            return Query()
    tx = _GoogleTransaction(Client(), transaction)
    tx.saved_word_project(pid, timeout=4)
    assert tx.saved_word_metadata(pid, limit=1001, timeout=3) == []
    assert calls == [
        ('root', {'field_paths': ['status', 'generation'], 'transaction': transaction, 'timeout': 4, 'retry': None}),
        ('select', ['generation', '_bm_saved_word_v1']), ('limit', 1001),
        ('stream', {'transaction': transaction, 'timeout': 3, 'retry': None}), ('closed',),
    ]
