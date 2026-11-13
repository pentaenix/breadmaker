"""Synthetic provider contracts. Intended for CI; no live data or credentials."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json
import pytest
from hosted.security import product_inventory as module

START = datetime(2026, 11, 13, tzinfo=timezone.utc)
END = START + timedelta(seconds=2)
PID = 'a' * 32

class Stream:
    def __init__(self, rows): self.rows, self.closed = iter(rows), False
    def __iter__(self): return self
    def __next__(self): return next(self.rows)
    def close(self): self.closed = True

class Client:
    def __init__(self, roots=None, count=2):
        self.roots = roots if roots is not None else [[(PID, 1)], [(PID, 1)]]
        self.count, self.calls, self.streams, self.scans = count, [], [], 0
    def collection(self, path):
        client = self
        class Query:
            aggregate = False
            def where(self, *, filter):
                assert path == 'projects' and filter == ('status', '==', 'active')
                return self
            def select(self, fields):
                assert fields == ['status', 'generation']
                return self
            def limit(self, value):
                assert value == (101 if path == 'projects' else 10001)
                return self
            def count(self, *, alias):
                assert alias == 'total' and (path == 'projects' or path.split('/')[-1] in module.COLLECTIONS.values())
                self.aggregate = True
                return self
            def stream(self, *, retry, timeout):
                assert retry is None and 0 < timeout <= 5
                client.calls.append(path)
                if path == 'projects' and self.aggregate:
                    rows = [[SimpleNamespace(alias='total', value=len(client.roots[client.scans-1]), read_time=START)]]
                elif path == 'projects':
                    roots = client.roots[client.scans]
                    client.scans += 1
                    rows = [SimpleNamespace(reference=SimpleNamespace(path=f'projects/{pid}'),
                            exists=True, read_time=START,
                            to_dict=lambda generation=g: {'status': 'active', 'generation': generation})
                            for pid, g in roots]
                else:
                    if isinstance(client.count, Exception): raise client.count
                    rows = [[SimpleNamespace(alias='total', value=client.count, read_time=START)]]
                stream = Stream(rows)
                client.streams.append(stream)
                return stream
        return Query()

def collect(monkeypatch, client, **kwargs):
    monkeypatch.setattr(module, '_active_status_filter', lambda: ('status', '==', 'active'))
    stamps = iter([START, END])
    snapshot = module.GoogleFirestoreProductInventoryProducer(client=client, enabled=True,
        utc_now=lambda: next(stamps), **kwargs).collect()
    return json.loads(snapshot.json_bytes(now=END))

def test_default_disabled_no_client_access():
    snapshot = module.GoogleFirestoreProductInventoryProducer(client=object(), utc_now=lambda: START).collect()
    assert set(json.loads(snapshot.json_bytes(now=END))['metrics'].values()) == {None}

def test_fixed_metadata_only_counts_and_closed_streams(monkeypatch):
    client = Client()
    result = collect(monkeypatch, client)
    assert result['coverage']['completeness'] == 'complete'
    assert result['metrics'] == {'current_cloud_projects': 1, **dict.fromkeys(module.COLLECTIONS, 2)}
    assert len(client.calls) == 10
    assert all(stream.closed for stream in client.streams)
    assert PID not in json.dumps(result)

@pytest.mark.parametrize('roots', [[[(PID, 1)], [(PID, 2)]], [[(PID, 1)], []], [[], [(PID, 1)]]])
def test_mutation_or_publication_invalidates_every_metric(monkeypatch, roots):
    result = collect(monkeypatch, Client(roots=roots))
    assert result['coverage']['completeness'] == 'unknown'
    assert set(result['metrics'].values()) == {None}

@pytest.mark.parametrize('count', [True, -1, 1.5, 10001, RuntimeError('private provider text')])
def test_failure_or_bound_is_unknown_never_partial_zero(monkeypatch, count):
    result = collect(monkeypatch, Client(count=count))
    assert set(result['metrics'].values()) == {None}

def test_no_active_projects_is_known_zero(monkeypatch):
    client = Client(roots=[[], []])
    assert set(collect(monkeypatch, client)['metrics'].values()) == {0}
    assert client.calls == ['projects'] * 4

def test_project_cap_fails_closed(monkeypatch):
    client = Client(roots=[[(f'{i:032x}', 1) for i in range(101)]])
    assert set(collect(monkeypatch, client)['metrics'].values()) == {None}
    assert client.calls == ['projects']

def test_stale_snapshot_does_not_refresh_counts():
    snapshot = module.ProductInventorySnapshot(START, END, dict.fromkeys(module.FIELDS, 0))
    result = json.loads(snapshot.json_bytes(now=END+timedelta(seconds=901)))
    assert set(result['metrics'].values()) == {None}
    assert result['as_of'] == '2026-11-13T00:00:02Z'
