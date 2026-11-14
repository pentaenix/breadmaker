"""Synthetic provider contracts. Intended for CI; no live data or credentials."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json
import pytest
from hosted.security import project_recency as module

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
        self.timeouts = []
        self.updated = START.isoformat()
    def collection(self, path):
        client = self
        class Query:
            aggregate = False
            def where(self, *, filter):
                assert path == 'projects' and filter == ('status', '==', 'active')
                return self
            def select(self, fields):
                assert fields == ['status', 'generation', 'updated_at']
                return self
            def limit(self, value):
                assert value == (101 if path == 'projects' else 10001)
                return self
            def count(self, *, alias):
                assert alias == 'total' and path == 'projects'
                self.aggregate = True
                return self
            def stream(self, *, retry, timeout):
                assert retry is None and 0 < timeout <= 5
                client.calls.append(path)
                client.timeouts.append(timeout)
                if path == 'projects' and self.aggregate:
                    rows = [[SimpleNamespace(alias='total', value=len(client.roots[client.scans-1]), read_time=START)]]
                elif path == 'projects':
                    roots = client.roots[client.scans]
                    client.scans += 1
                    rows = [SimpleNamespace(reference=SimpleNamespace(path=f'projects/{pid}'),
                            exists=True, read_time=START,
                            to_dict=lambda generation=g: {'status': 'active', 'generation': generation, 'updated_at': client.updated})
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
    snapshot = module.GoogleFirestoreProjectRecencyProducer(client=client, enabled=True,
        utc_now=lambda: next(stamps), **kwargs).collect()
    return json.loads(snapshot.json_bytes(now=END))

def test_disabled_never_touches_client():
    result = module.GoogleFirestoreProjectRecencyProducer(client=object(), utc_now=lambda: START).collect()
    assert result.values is None

@pytest.mark.parametrize('age,expected', [(0,(1,1,1)), (86400,(1,1,1)),
    (86401,(0,1,1)), (604800,(0,1,1)), (604801,(0,0,1)),
    (2592000,(0,0,1)), (2592001,(0,0,0))])
def test_exact_windows_metadata_only(monkeypatch,age,expected):
    client=Client();client.updated=(START-timedelta(seconds=age)).isoformat()
    result=collect(monkeypatch,client)
    assert result['metrics']==dict(zip(module.FIELDS,(1,*expected)))
    assert result['source']=='cloud_project_recency_v1'
    assert result['coverage']['completeness']=='complete'
    assert len(client.calls)==4 and set(client.calls)=={'projects'}
    assert all(stream.closed for stream in client.streams)
    assert PID not in json.dumps(result)

@pytest.mark.parametrize('stamp',[None, 0, '', 'yesterday', '2026-11-13T00:00:00',
    '2026-11-13T00:00:00+01:00', '2026-11-14T00:00:00Z'])
def test_missing_invalid_or_future_timestamp_is_unknown(monkeypatch,stamp):
    client=Client();client.updated=stamp
    assert set(collect(monkeypatch,client)['metrics'].values())=={None}

@pytest.mark.parametrize('roots', [[[(PID,1)],[(PID,2)]], [[(PID,1)],[]], [[],[(PID,1)]]])
def test_changed_population_or_generation_is_unknown(monkeypatch,roots):
    assert set(collect(monkeypatch,Client(roots=roots))['metrics'].values())=={None}

def test_empty_population_has_timestamp_evidence(monkeypatch):
    client=Client(roots=[[],[]])
    assert set(collect(monkeypatch,client)['metrics'].values())=={0}
    assert len(client.calls)==4

def test_cap_is_unknown(monkeypatch):
    client=Client(roots=[[(f'{i:032x}',1) for i in range(101)]])
    assert set(collect(monkeypatch,client)['metrics'].values())=={None}

def test_deadline_and_staleness(monkeypatch):
    monkeypatch.setattr(module,'_active_status_filter',lambda:('status','==','active'))
    client=Client();stamps=iter([START,END])
    producer=module.GoogleFirestoreProjectRecencyProducer(client=client,enabled=True,
        utc_now=lambda:next(stamps),monotonic=lambda:10.)
    assert producer.collect(deadline=9.).values is None
    assert client.calls==[]
    snapshot=module.ProjectRecencySnapshot(START,END,dict.fromkeys(module.FIELDS,0))
    assert set(json.loads(snapshot.json_bytes(now=END+timedelta(seconds=901)))['metrics'].values())=={None}

@pytest.mark.parametrize('values', [(101,0,0,0),(1,2,2,2),(1,1,0,1),(1,0,1,0)])
def test_snapshot_rejects_impossible_totals(values):
    with pytest.raises(ValueError):module.ProjectRecencySnapshot(START,END,dict(zip(module.FIELDS,values)))

def test_updated_timestamp_change_without_generation_change_is_unknown(monkeypatch):
    class Changed(module.GoogleFirestoreProjectRecencyProducer):
        calls=0
        def _roots(self, timeout):
            self.calls+=1
            return {PID:(1,START-timedelta(days=self.calls))},[START]
    stamps=iter([START,END])
    result=Changed(client=object(),enabled=True,utc_now=lambda:next(stamps)).collect()
    assert result.values is None
