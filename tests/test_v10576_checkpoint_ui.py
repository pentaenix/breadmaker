"""Checkpoint projections retain immutable data, authority and lineage fences."""
from copy import deepcopy
import pytest
from fastapi import HTTPException
from hosted.checkpoint_ui import HostedCheckpointUI
from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, ProjectUnavailable
from tests.test_v10354 import _services, OWNER, READER


@pytest.fixture
def checkpoint_env():
    docs,objects,authority,users,current,projects=_services()
    pid=projects.create('Checkpoint QA')['id'];domain=projects.domain
    tab=next(item for item in domain.list('document_tab',{'project_id':pid},user_id=OWNER) if item['id']=='tab_manuscript')
    chapter=domain.create('chapter',{'project_id':pid},{'title':'Harbor','tab_id':tab['id'],'position':0},user_id=OWNER)
    block=domain.create('manuscript_block',{'project_id':pid},{'chapter_id':chapter['id'],'tab_id':tab['id'],'block_type':'paragraph','semantic_style':'body','position':0,'text_content':'The quiet harbor','content_html':'The quiet harbor'},user_id=OWNER)
    def capture(project_id):
        assert project_id==pid
        return {'project':projects.get(pid),'tabs':domain.list('document_tab',{'project_id':pid},user_id=OWNER),
            'chapters':domain.list('chapter',{'project_id':pid},user_id=OWNER),'blocks':domain.list('manuscript_block',{'project_id':pid},user_id=OWNER)}
    ui=HostedCheckpointUI(domain,pid,OWNER,capture)
    # The pre-fix live HTTP implementation stored a raw project snapshot without
    # source/word metadata. Reading it must not rewrite that immutable object.
    row=domain.create('checkpoint',{'project_id':pid},{'scope':'chapter','chapter_id':chapter['id'],'name':'Before','snapshot':capture(pid)},user_id=OWNER)
    return domain,pid,chapter,block,ui,row,capture


def test_legacy_checkpoint_projection_is_read_only_and_keeps_source(checkpoint_env):
    domain,pid,chapter,block,ui,row,capture=checkpoint_env
    before=domain.project_generation(pid,OWNER)
    saved=ui.get(row['id'])
    assert saved['checkpoint']['word_count']==3
    assert saved['checkpoint']['current_chapter_id']==chapter['id']
    assert saved['checkpoint']['source_exists'] is True
    assert saved['snapshot']['blocks'][0]['text_content']=='The quiet harbor'
    assert domain.get('checkpoint',{'project_id':pid,'checkpoint_id':row['id']},user_id=OWNER)['payload_object']==row['payload_object']
    assert domain.project_generation(pid,OWNER)==before


def test_checkpoint_projection_never_reads_objects_for_nonmembers(checkpoint_env,monkeypatch):
    domain,pid,chapter,block,ui,row,capture=checkpoint_env
    def forbidden(*args,**kwargs):raise AssertionError('Unauthorized immutable object read')
    monkeypatch.setattr(domain.objects,'read_chunks',forbidden)
    outsider=HostedCheckpointUI(domain,pid,READER,capture)
    for action in (lambda:outsider.get(row['id']),lambda:outsider.list({}),lambda:outsider.compare(row['id'],'current')):
        with pytest.raises(ProjectUnavailable,match='PROJECT_NOT_FOUND'):action()


def test_deleted_source_stays_readable_but_cannot_compare_as_current(checkpoint_env):
    domain,pid,chapter,block,ui,row,capture=checkpoint_env
    domain.delete('chapter',{'project_id':pid,'chapter_id':chapter['id']},user_id=OWNER)
    before=domain.project_generation(pid,OWNER)
    saved=ui.get(row['id'])
    assert saved['checkpoint']['source_exists'] is False
    assert saved['checkpoint']['current_chapter_id'] is None
    assert saved['snapshot']['blocks'][0]['text_content']=='The quiet harbor'
    assert ui.list({'chapter_id':chapter['id']})==[]
    with pytest.raises(HTTPException) as failure:ui.compare(row['id'],'current')
    assert failure.value.status_code==409
    assert domain.project_generation(pid,OWNER)==before


def test_checkpoint_read_rejects_concurrent_source_change(checkpoint_env,monkeypatch):
    domain,pid,chapter,block,ui,row,capture=checkpoint_env
    original=domain.get;changed=False
    def racing(kind,ids,**kwargs):
        nonlocal changed
        if kind=='chapter' and not changed:
            changed=True
            domain.update('chapter',{'project_id':pid,'chapter_id':chapter['id']},{'title':'Concurrent edit'},user_id=OWNER)
        return original(kind,ids,**kwargs)
    monkeypatch.setattr(domain,'get',racing)
    with pytest.raises(DocumentConflict,match='SOURCE_CHANGED'):ui.get(row['id'])


def test_large_comparison_refuses_without_truncation_or_mutation(checkpoint_env):
    domain,pid,chapter,block,ui,row,capture=checkpoint_env
    snap=deepcopy(capture(pid));next(item for item in snap['blocks'] if item['id']==block['id'])['text_content']='word '*2000
    large=domain.create('checkpoint',{'project_id':pid},{'scope':'chapter','chapter_id':chapter['id'],'snapshot':snap},user_id=OWNER)
    before=domain.project_generation(pid,OWNER)
    with pytest.raises(HTTPException) as failure:ui.compare(large['id'],'current')
    assert failure.value.status_code==413
    assert len(ui.get(large['id'])['snapshot']['blocks'][0]['text_content'])==10_000
    assert domain.project_generation(pid,OWNER)==before


def test_book_comparison_supports_opaque_chapter_ids_and_escapes_text(checkpoint_env):
    domain,pid,chapter,block,ui,row,capture=checkpoint_env
    book=domain.create('checkpoint',{'project_id':pid},{'scope':'book','snapshot':capture(pid)},user_id=OWNER)
    domain.update('manuscript_block',{'project_id':pid,'block_id':block['id']},{'text_content':'The <script>harbor</script>','content_html':'The &lt;script&gt;harbor&lt;/script&gt;'},user_id=OWNER)
    result=ui.compare(book['id'],'current')
    assert result['scope']=='book' and result['change_count']>0
    assert '<script>' not in str(result['rows'])
    assert '&lt;' in str(result['rows'])
