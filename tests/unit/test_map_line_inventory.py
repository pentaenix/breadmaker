"""Synthetic provider only: bounded parent discovery, no private field queries."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json
import pytest
from hosted.security import map_line_inventory as module
from hosted.security import product_inventory as roots_module
START=datetime(2026,11,14,tzinfo=timezone.utc)
END=START+timedelta(seconds=2)
PID='a'*32

class Stream:
    def __init__(self,rows):self.rows=iter(rows);self.closed=False
    def __iter__(self):return self
    def __next__(self):return next(self.rows)
    def close(self):self.closed=True

class Client:
    def __init__(self,*,projects=None,maps=None,lines=2,fault=None):
        self.projects=projects if projects is not None else [[(PID,1)],[(PID,1)]]
        self.maps=maps if maps is not None else {PID:[['map_one'],['map_one']]}
        self.lines,self.fault=lines,fault
        self.calls,self.streams,self.timeouts=[],[],[]
        self.root_scans=0;self.parent_scans={}
    def collection(self,path):
        client=self
        class Query:
            projection=None;aggregate=False;bound=None
            def where(self,*,filter):
                assert path=='projects' and filter==('status','==','active')
                return self
            def select(self,fields):
                assert fields==(['status','generation'] if path=='projects' else [])
                self.projection=fields;return self
            def limit(self,value):
                assert (value==10001 if path.endswith('/lines') else
                        1<=value<=101 if path.endswith('/story_maps') and self.projection==[] else value==101)
                self.bound=value;return self
            def count(self,*,alias):
                assert alias=='total';self.aggregate=True;return self
            def stream(self,*,retry,timeout):
                assert retry is None and 0<timeout<=5
                client.calls.append((path,self.projection,self.aggregate,self.bound));client.timeouts.append(timeout)
                if client.fault=='provider':raise RuntimeError('private provider text')
                if path=='projects':
                    if not self.aggregate:client.root_scans+=1
                    rows=client.projects[client.root_scans-1]
                    count=len(rows)
                    values=[(f'projects/{pid}',{'status':'active','generation':gen}) for pid,gen in rows]
                elif path.endswith('/story_maps'):
                    pid=path.split('/')[1]
                    if not self.aggregate:client.parent_scans[pid]=client.parent_scans.get(pid,0)+1
                    rows=client.maps.get(pid,[[],[]])[client.parent_scans[pid]-1]
                    if not self.aggregate:rows=rows[:self.bound]
                    count=len(rows);values=[(path+'/'+mid,{}) for mid in rows]
                    if client.fault=='content_projection':values=[(path+'/map_one',{'title':'private'})]
                else:
                    assert path.endswith('/lines') and self.aggregate and self.projection is None
                    count=client.lines;values=[]
                stamp=START-timedelta(seconds=1) if client.fault=='stale_time' else START
                if self.aggregate:
                    rows=[[SimpleNamespace(alias='total',value=count,read_time=stamp)]]
                    if client.fault=='empty_count':rows=[]
                    if client.fault=='bad_alias':rows[0][0].alias='private'
                else:
                    rows=[SimpleNamespace(reference=SimpleNamespace(path=p),exists=True,
                        read_time=stamp,to_dict=lambda d=data:d) for p,data in values]
                stream=Stream(rows)
                if client.fault=='iteration':
                    def broken():
                        raise RuntimeError('private stream error')
                        yield
                    stream.rows=broken()
                client.streams.append(stream);return stream
        return Query()

def collect(monkeypatch,client,*,deadline=None,monotonic=lambda:0.):
    monkeypatch.setattr(roots_module,'_active_status_filter',lambda:('status','==','active'))
    stamps=iter([START,END])
    return module.GoogleFirestoreMapLineInventoryProducer(client=client,enabled=True,
        utc_now=lambda:next(stamps),monotonic=monotonic).collect(deadline=deadline)

def test_off_no_client():
    assert module.GoogleFirestoreMapLineInventoryProducer(client=object(),utc_now=lambda:START).collect().values is None

def test_exact_current_line_count_names_only_and_private_output(monkeypatch):
    client=Client();snapshot=collect(monkeypatch,client)
    assert dict(snapshot.values)==dict(zip(module.FIELDS,(1,1,2)))
    assert len(client.calls)==9
    assert all(s.closed for s in client.streams)
    assert all(projection==[] for path,projection,aggregate,_ in client.calls if path.endswith('/story_maps') and not aggregate)
    payload=json.loads(snapshot.json_bytes(now=END))
    assert payload['source']=='cloud_map_line_inventory_v1'
    assert PID not in json.dumps(payload) and 'map_one' not in json.dumps(payload)

@pytest.mark.parametrize('projects,maps,expected,calls', [([[],[]],{},(0,0,0),4),
    ([[(PID,1)],[(PID,1)]],{PID:[[],[]]},(1,0,0),8)])
def test_empty_population_requires_count_observation(monkeypatch,projects,maps,expected,calls):
    client=Client(projects=projects,maps=maps)
    assert tuple(collect(monkeypatch,client).values.values())==expected
    assert len(client.calls)==calls

@pytest.mark.parametrize('fault',['provider','iteration','stale_time','empty_count','bad_alias','content_projection'])
def test_failure_never_exposes_partial_or_private_data(monkeypatch,fault):
    client=Client(fault=fault);snapshot=collect(monkeypatch,client)
    payload=json.loads(snapshot.json_bytes(now=END))
    assert set(payload['metrics'].values())=={None}
    assert 'private' not in json.dumps(payload)
    assert all(s.closed for s in client.streams)

@pytest.mark.parametrize('lines',[True,-1,1.5,10001])
def test_child_count_bounds(monkeypatch,lines):
    assert collect(monkeypatch,Client(lines=lines)).values is None

@pytest.mark.parametrize('maps',[[['map_one'],[]],[['map_one'],['map_two']],
    [['map_one','map_one'],['map_one']],[[f'map_{i}' for i in range(101)],[]]])
def test_parent_change_duplicate_or_cap_is_unknown(monkeypatch,maps):
    assert collect(monkeypatch,Client(maps={PID:maps})).values is None

def test_parent_cap_is_fleet_wide_not_per_project(monkeypatch):
    other='b'*32;projects=[[(PID,1),(other,1)]]*2
    maps={PID:[[f'map_{i}' for i in range(60)]]*2,other:[[f'map_{i}' for i in range(41)]]*2}
    assert collect(monkeypatch,Client(projects=projects,maps=maps)).values is None

def test_generation_change_invalidates_all(monkeypatch):
    assert collect(monkeypatch,Client(projects=[[(PID,1)],[(PID,2)]])).values is None

def test_full_bound_uses_exact_504_rpcs(monkeypatch):
    pids=[f'{i:032x}' for i in range(100)]
    client=Client(projects=[[(pid,1) for pid in pids]]*2,
        maps={pid:[['map_one']]*2 for pid in pids},lines=10000)
    assert collect(monkeypatch,client).values['current_map_lines']==1000000
    assert len(client.calls)==504

def test_deadline_clamps_and_expiry_stops_new_queries(monkeypatch):
    client=Client();assert collect(monkeypatch,client,deadline=.25).values is not None
    assert client.timeouts==[.25]*9
    client=Client();ticks=iter([0.,0.,60.])
    assert collect(monkeypatch,client,monotonic=lambda:next(ticks)).values is None
    assert len(client.calls)==1 and all(s.closed for s in client.streams)
    client=Client();assert collect(monkeypatch,client,deadline=-1).values is None
    assert client.calls==[]

def test_stale_and_impossible_snapshot():
    snapshot=module.MapLineInventorySnapshot(START,END,dict(zip(module.FIELDS,(1,1,2))))
    assert set(json.loads(snapshot.json_bytes(now=END+timedelta(seconds=901)))['metrics'].values())=={None}
    for values in [(0,1,0),(1,101,0),(1,1,10001)]:
        with pytest.raises(ValueError):module.MapLineInventorySnapshot(START,END,dict(zip(module.FIELDS,values)))


def test_parent_query_limit_shrinks_with_fleet_budget(monkeypatch):
    other='b'*32
    client=Client(projects=[[(PID,1),(other,1)]]*2,
        maps={PID:[[f'map_{i}' for i in range(99)]]*2,other:[['map_one']]*2})
    assert collect(monkeypatch,client).values['current_story_maps']==100
    bounds=[bound for path,projection,aggregate,bound in client.calls
            if path.endswith('/story_maps') and not aggregate]
    assert bounds==[101,2,101,2]
