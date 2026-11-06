"""Sparse hosted UI records must survive native import and hosted round trips."""
import io
import zipfile
import pytest
from hosted.local_portability import LocalPortabilityError
from hosted.packages import HostedProjectPackageService
from hosted.runtime_services import _DEV_USER_ID as OWNER
from tests.test_v10369 import _project,_create,_restore
from tests.test_v10588_readable_export import _image


def test_sparse_canvas_and_populated_map_line_round_trip(monkeypatch,tmp_path):
    runtime,pid,chapter=_project(monkeypatch);parents={'project_id':pid}
    first=_create(runtime,'canvas',parents,{'name':'First sparse','canvas_type':'blank'})
    child=_create(runtime,'canvas',parents,{'name':'Child sparse','parent_id':first['id'],'canvas_type':'blank'})
    cp={**parents,'canvas_id':child['id']}
    a=_create(runtime,'canvas_node',cp,{'title':'Opening','x':17,'y':35,'chapter_id':chapter['id']})
    b=_create(runtime,'canvas_node',cp,{'title':'Crisis','x':640,'y':350})
    _create(runtime,'canvas_edge',cp,{'from_node_id':a['id'],'to_node_id':b['id'],'label':'Retained connector'})
    story=_create(runtime,'story_map',parents,{'name':'Story'})
    _create(runtime,'map_line',{**parents,'map_id':story['id']},{'name':'Main','title':'Main','position':0,'provenance':'source','layout_locked':True})
    from hosted.http_api import _MAP_LINE_DEFAULTS
    assert _MAP_LINE_DEFAULTS['color']=='#4f7d8a'
    image=_image()
    with runtime.context.bind(pid,user_id=OWNER):asset=runtime.assets.put_blob(image,'.png')
    _create(runtime,'manuscript_block',parents,{'chapter_id':chapter['id'],'tab_id':chapter['tab_id'],'block_type':'image','image_path':asset.api_path,'image_description':'Preserved native image','position':99})
    service=HostedProjectPackageService(runtime.projects);payload=service.export_local_bytes(pid,OWNER)
    with _restore(tmp_path,payload) as db:
        rows=db.execute('SELECT id,parent_id,name,position FROM canvases ORDER BY id').fetchall()
        by_name={row[2]:row for row in rows}
        assert by_name['First sparse'][3]==by_name['Child sparse'][3]==0
        assert by_name['Child sparse'][1]==by_name['First sparse'][0]
        assert db.execute('SELECT name,color,provenance,layout_locked FROM map_lines').fetchone()==('Main','#4f7d8a','source',1)
        assert db.execute('SELECT title,x,y FROM canvas_nodes ORDER BY x').fetchall()==[('Opening',17,35),('Crisis',640,350)]
        assert db.execute('SELECT label FROM canvas_edges').fetchone()[0]=='Retained connector'
        assert db.execute('SELECT image_description FROM manuscript_blocks WHERE block_type=\'image\'').fetchone()[0]=='Preserved native image'
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    restored=service.import_bytes(payload,OWNER,operation='synthetic_sparse_native_roundtrip')
    rp={'project_id':restored['id']};domain=runtime.projects.domain
    canvases=domain.list('canvas',rp,user_id=OWNER);named={row['name']:row for row in canvases}
    assert named['Child sparse']['parent_id']==named['First sparse']['id']
    mapping=domain.list('story_map',rp,user_id=OWNER)[0]
    line=domain.list('map_line',{**rp,'map_id':mapping['id']},user_id=OWNER)[0]
    assert (line['name'],line['color'])==('Main','#4f7d8a')
    assert service.export_local_bytes(restored['id'],OWNER)
    with zipfile.ZipFile(io.BytesIO(service.export_bytes(restored['id'],OWNER))) as archive:
        assert [archive.read(name) for name in archive.namelist() if name.startswith('assets/')]==[image]


def test_conflicting_map_alias_is_not_silently_discarded(monkeypatch):
    runtime,pid,_=_project(monkeypatch);parents={'project_id':pid}
    story=_create(runtime,'story_map',parents,{'name':'Story'})
    _create(runtime,'map_line',{**parents,'map_id':story['id']},{'name':'Current','title':'Different data','color':'#112233'})
    with pytest.raises(LocalPortabilityError,match='HOSTED_PORTABLE_TITLE_ALIAS_CONFLICT:map_lines'):
        HostedProjectPackageService(runtime.projects).export_local_bytes(pid,OWNER)


def test_explicit_invalid_canvas_position_is_not_defaulted(monkeypatch):
    runtime,pid,_=_project(monkeypatch)
    _create(runtime,'canvas',{'project_id':pid},{'name':'Bad position','position':'not-a-number'})
    with pytest.raises(LocalPortabilityError,match='HOSTED_PORTABLE_INTEGER_REFERENCE_INVALID:canvases.position'):
        HostedProjectPackageService(runtime.projects).export_local_bytes(pid,OWNER)
