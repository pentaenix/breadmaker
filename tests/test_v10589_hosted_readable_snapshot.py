"""Existing hosted Canvas/Map/history must not block readable copies or be lost."""
import io
import json
import zipfile

import pytest
from docx import Document

from hosted.firestore.contracts import DocumentConflict
from hosted.packages import HostedProjectPackageService
from hosted.readable_export import _readable_zip
from hosted.runtime_services import _DEV_USER_ID as OWNER
from tests.test_v10364 import _replicas
from tests.test_v10588_readable_export import _image


def test_readable_hosted_sparse_canvas_map_and_frozen_history_restore_unchanged(monkeypatch):
    one,make,_,pid,cid=_replicas(monkeypatch);domain=one.projects.domain
    parents={'project_id':pid}
    def create(logical,ids,data):return domain.create(logical,ids,data,user_id=OWNER)
    # Real hosted UI writes omit Canvas/Map position and retain Map line title.
    canvas=create('canvas',parents,{'name':'Sparse canvas','description':'','canvas_type':'blank'})
    cp={**parents,'canvas_id':canvas['id']}
    version=create('canvas_version',cp,{'name':'Frozen geometry'})
    create('canvas_version_item',{**cp,'version_id':version['id']},
        {'kind':'node','source_id':'node_original','payload':{'title':'Preserved history','x':17}})
    story_map=create('story_map',parents,{'name':'Sparse map'})
    create('map_line',{**parents,'map_id':story_map['id']},{'name':'Main','title':'Main','position':0,'color':'#333333'})
    tab=create('document_tab',parents,{'title':'Additional notes','position':1,'is_manuscript':False})
    chapter=create('chapter',parents,{'tab_id':tab['id'],'title':'Note chapter','position':0})
    create('manuscript_block',parents,{'chapter_id':chapter['id'],'tab_id':tab['id'],
        'text_content':'Second document','content_html':'<strong>Second</strong> document','position':0})
    payload=_image()
    with one.context.bind(pid,user_id=OWNER):saved=one.assets.put_blob(payload,'.png')
    first=next(ch for ch in domain.list('chapter',parents,user_id=OWNER) if ch['id']==cid)
    create('manuscript_block',parents,{'chapter_id':cid,'tab_id':first['tab_id'],
        'block_type':'image','image_path':saved.api_path,'image_description':'Original image','position':99})
    service=HostedProjectPackageService(one.projects);captured=[];native=service.export_bytes
    def freeze(self,*args):
        raw=native(*args);captured.append(raw);return raw
    monkeypatch.setattr(HostedProjectPackageService,'export_bytes',freeze)
    _,data=_readable_zip(one.projects,pid,OWNER)
    assert len(captured)==1
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.read('Project.breadmaker')==captured[0]
        assert archive.read('assets/'+saved.api_path.rsplit('/',1)[-1])==payload
        assert 'not a desktop-native' in archive.read('README.txt').decode()
        text=''.join(archive.read(n).decode() for n in archive.namelist() if n.startswith('documents/') and n.endswith('.txt'))
        assert 'Second document' in text
        assert len([n for n in archive.namelist() if n.endswith('.docx')])==2
        docs=[Document(io.BytesIO(archive.read(n))) for n in archive.namelist() if n.endswith('.docx')]
        assert sum(len(doc.inline_shapes) for doc in docs)==1
        reference=json.loads(archive.read('data/project-content.json'))['tables']
        assert reference['canvases'][0]['name']=='Sparse canvas'
        assert reference['map_lines'][0]['title']=='Main'
        assert not {'review_participants','project_memberships','ui_state','bookmarks'} & set(reference)
        restored=HostedProjectPackageService(make().projects).import_bytes(archive.read('Project.breadmaker'),OWNER)
    restored_domain=make().projects.domain;rp={'project_id':restored['id']}
    restored_canvas=restored_domain.list('canvas',rp,user_id=OWNER)[0]
    rv=restored_domain.list('canvas_version',{**rp,'canvas_id':restored_canvas['id']},user_id=OWNER)[0]
    items=restored_domain.list('canvas_version_item',{**rp,'canvas_id':restored_canvas['id'],'version_id':rv['id']},user_id=OWNER)
    assert items[0]['payload']=={'title':'Preserved history','x':17}
    restored_map=restored_domain.list('story_map',rp,user_id=OWNER)[0]
    assert restored_domain.list('map_line',{**rp,'map_id':restored_map['id']},user_id=OWNER)[0]['title']=='Main'
    # Native portability remains fail-closed for genuinely different history.
    with pytest.raises(ValueError,match='HOSTED_PORTABLE_CANVAS_VERSION_SHAPE_UNSUPPORTED'):
        service.export_local_bytes(pid,OWNER)


def test_hosted_readable_rejects_mutation_during_capture_before_render(monkeypatch):
    one,_,_,pid,_=_replicas(monkeypatch);native=HostedProjectPackageService.export_bytes
    def changed(self,*args):
        raw=native(self,*args)
        self.projects.domain.create('task',{'project_id':pid},{'title':'Concurrent edit'},user_id=OWNER)
        return raw
    monkeypatch.setattr(HostedProjectPackageService,'export_bytes',changed)
    monkeypatch.setattr('hosted.readable_export.write_hosted_readable_archive',lambda *args:pytest.fail('mixed snapshot rendered'))
    with pytest.raises(DocumentConflict,match='HOSTED_READABLE_CONCURRENT_MUTATION'):
        _readable_zip(one.projects,pid,OWNER)


def test_hosted_readable_does_not_requery_live_content_after_capture(monkeypatch):
    import formats.readable_archive as renderer
    one,_,_,pid,cid=_replicas(monkeypatch);domain=one.projects.domain;native=renderer.docx_bytes
    block=domain.list('manuscript_block',{'project_id':pid},user_id=OWNER)[0]
    domain.update('manuscript_block',{'project_id':pid,'block_id':block['id']},
        {'text_content':'Frozen before','content_html':'Frozen before'},user_id=OWNER)
    changed=[]
    def render(*args):
        if not changed:
            changed.append(True)
            domain.update('manuscript_block',{'project_id':pid,'block_id':block['id']},
                {'text_content':'Live after','content_html':'Live after'},user_id=OWNER)
        return native(*args)
    monkeypatch.setattr(renderer,'docx_bytes',render)
    _,data=_readable_zip(one.projects,pid,OWNER)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        text=archive.read(next(n for n in archive.namelist() if n.startswith('documents/') and n.endswith('.txt'))).decode()
        assert 'Frozen before' in text and 'Live after' not in text
        assert 'Live after' not in archive.read('data/project-content.json').decode()
