"""Incremental derived caches stay bounded, race-safe and outside manuscript Undo."""
from copy import deepcopy

import pytest

from hosted.action_journal import ActionJournal,bind_action
from hosted.chapter_mutations import replace_chapter_blocks
from hosted.document_import import DocumentImport
from hosted.firestore.contracts import DocumentConflict,ProjectUnavailable
from hosted.manuscript_read_cache import refresh_chapter_read_cache
from hosted.runtime_services import _DEV_USER_ID as OWNER
from tests.test_v10364 import _replicas


def imported(monkeypatch,count=130):
    one,make,docs,_,_=_replicas(monkeypatch);service=DocumentImport(one.projects)
    progress=service.begin('\n'.join(f'Synthetic paragraph {i}' for i in range(count)).encode(),
        'Synthetic.txt','heading1','Synthetic incremental cache',OWNER,'op_cache_fixture')
    while not progress['ready']:progress=service.step(OWNER,progress['job_id'],progress['next_batch'])
    pid=service.finish(OWNER,progress['job_id'])['project']['id']
    cid=one.projects.domain.list('chapter',{'project_id':pid},user_id=OWNER)[0]['id']
    return one,make,docs,pid,cid


def edit(one,pid,cid,text,op):
    domain=one.projects.domain
    rows=domain.list('manuscript_block',{'project_id':pid},user_id=OWNER,field='chapter_id',value=cid)
    blocks=[{key:row[key] for key in ('id','text_content','content_html')} for row in rows]
    blocks[0].update(text_content=text,content_html=text)
    return replace_chapter_blocks(domain,project_id=pid,chapter_id=cid,user_id=OWNER,
        blocks=blocks,expected_revision=domain.project_generation(pid,OWNER),operation=op)


def test_repeated_edits_refresh_only_one_chunk_without_growing_read_fanout(monkeypatch):
    one,make,docs,pid,cid=imported(monkeypatch);domain=one.projects.domain
    baseline={row.path:deepcopy(row.data) for row in docs.query_collection(('projects',pid,'blocks'))}
    for index in range(6):
        saved=edit(one,pid,cid,f'Synthetic edit {index}',f'op_incremental_{index}')
        hints=set();domain.list('manuscript_block',{'project_id':pid},user_id=OWNER,
            field='chapter_id',value=cid,cache_refresh_chapters=hints)
        assert hints=={cid}
        root=deepcopy(docs.get(('projects',pid)).data)
        reads=[];native=domain.objects.read_chunks
        with monkeypatch.context() as scope:
            def counted(*args,**kwargs):reads.append(args[0]);return native(*args,**kwargs)
            scope.setattr(domain.objects,'read_chunks',counted)
            result=refresh_chapter_read_cache(domain,project_id=pid,chapter_id=cid,user_id=OWNER)
        # Up to3 source objects (two old bundles + the edited paragraph), plus
        # publication verification and an independent new-bundle readback.
        assert result=={'updated':64,'more':False} and len(reads)<=5
        assert docs.get(('projects',pid)).data==root
        rebuilt=make().projects.domain;hints=set()
        rows=rebuilt.list('manuscript_block',{'project_id':pid},user_id=OWNER,cache_refresh_chapters=hints)
        assert rows[0]['text_content']==f'Synthetic edit {index}' and not hints
        assert len(rows)==130 and len({row['id'] for row in rows})==130
        assert one.projects.get(pid)['generation']==saved['generation']
    for path,before in baseline.items():
        after=dict(docs.get(path).data)
        if before['position']>=64:assert after==before


def test_failed_cache_publication_cannot_fail_or_revert_a_completed_save(monkeypatch):
    one,make,docs,pid,cid=imported(monkeypatch)
    saved=edit(one,pid,cid,'Durably saved despite cache outage','op_cache_outage')
    before={row.path:deepcopy(row.data) for row in docs.query_collection(('projects',pid,'blocks'))}
    def outage(*args,**kwargs):raise RuntimeError('Synthetic cache provider outage')
    monkeypatch.setattr(one.projects.domain,'_publish_hybrid',outage)
    with pytest.raises(RuntimeError,match='provider outage'):
        refresh_chapter_read_cache(one.projects.domain,project_id=pid,chapter_id=cid,user_id=OWNER)
    assert {row.path:row.data for row in docs.query_collection(('projects',pid,'blocks'))}==before
    assert make().projects.get(pid)['generation']==saved['generation']
    rows=make().projects.domain.list('manuscript_block',{'project_id':pid},user_id=OWNER)
    assert rows[0]['text_content']=='Durably saved despite cache outage'


def test_concurrent_edit_is_skipped_and_next_step_resumes_current_text(monkeypatch):
    one,make,_,pid,cid=imported(monkeypatch);domain=one.projects.domain
    edit(one,pid,cid,'First committed edit','op_cache_race_first')
    native=domain._publish_hybrid;fired=[]
    def race(*args,**kwargs):
        pointer=native(*args,**kwargs)
        if not fired:
            fired.append(True);edit(one,pid,cid,'Newer edit wins','op_cache_race_newer')
        return pointer
    with monkeypatch.context() as scope:
        scope.setattr(domain,'_publish_hybrid',race)
        result=refresh_chapter_read_cache(domain,project_id=pid,chapter_id=cid,user_id=OWNER)
    assert result=={'updated':63,'more':True}
    assert refresh_chapter_read_cache(make().projects.domain,project_id=pid,chapter_id=cid,user_id=OWNER)=={'updated':64,'more':False}
    assert make().projects.domain.list('manuscript_block',{'project_id':pid},user_id=OWNER)[0]['text_content']=='Newer edit wins'


def test_editor_authority_is_current_before_publication_and_attach(monkeypatch):
    one,_,docs,pid,cid=imported(monkeypatch);domain=one.projects.domain
    foreign='usr_'+'f'*32
    with pytest.raises(ProjectUnavailable):refresh_chapter_read_cache(domain,project_id=pid,chapter_id=cid,user_id=foreign)
    path=('projects',pid,'members',foreign)
    docs.run_transaction(lambda tx:tx.create(path,{'user_id':foreign,'role':'editor'}))
    edit(one,pid,cid,'Owner edit awaiting cache','op_cache_editor')
    native=domain._publish_hybrid
    def revoke(*args,**kwargs):
        pointer=native(*args,**kwargs);docs.run_transaction(lambda tx:tx.delete(path));return pointer
    before={row.path:deepcopy(row.data) for row in docs.query_collection(('projects',pid,'blocks'))}
    with monkeypatch.context() as scope:
        scope.setattr(domain,'_publish_hybrid',revoke)
        with pytest.raises(ProjectUnavailable):refresh_chapter_read_cache(domain,project_id=pid,chapter_id=cid,user_id=foreign)
    assert {row.path:row.data for row in docs.query_collection(('projects',pid,'blocks'))}==before
    docs.run_transaction(lambda tx:tx.create(path,{'user_id':foreign,'role':'editor'}))
    assert refresh_chapter_read_cache(domain,project_id=pid,chapter_id=cid,user_id=foreign)['updated']==64


def test_cache_between_action_writes_and_undo_redo_preserves_journal_authority(monkeypatch):
    one,make,docs,pid,cid=imported(monkeypatch);domain=one.projects.domain
    journal=ActionJournal(domain.documents);token=journal.begin(pid,OWNER,'Synthetic chapter action')['token']
    with bind_action(token,pid,OWNER):
        edit(one,pid,cid,'First action edit','op_action_cache_first')
        assert refresh_chapter_read_cache(domain,project_id=pid,chapter_id=cid,user_id=OWNER)['updated']==64
        edit(one,pid,cid,'Second action edit','op_action_cache_second')
    journal.finish(pid,OWNER,token)
    refresh_chapter_read_cache(domain,project_id=pid,chapter_id=cid,user_id=OWNER)
    journal.restore(pid,OWNER,token,'undo')
    assert make().projects.domain.list('manuscript_block',{'project_id':pid},user_id=OWNER)[0]['text_content']=='Synthetic paragraph 0'
    journal.restore(pid,OWNER,token,'redo')
    refresh_chapter_read_cache(domain,project_id=pid,chapter_id=cid,user_id=OWNER)
    assert make().projects.domain.list('manuscript_block',{'project_id':pid},user_id=OWNER)[0]['text_content']=='Second action edit'


@pytest.mark.parametrize('field',['generation','position'])
def test_cache_exclusion_never_ignores_canonical_journal_conflicts(monkeypatch,field):
    one,_,docs,pid,cid=imported(monkeypatch);domain=one.projects.domain
    journal=ActionJournal(domain.documents);token=journal.begin(pid,OWNER,'Canonical conflict')['token']
    with bind_action(token,pid,OWNER):edit(one,pid,cid,'Journal edit','op_journal_cache_conflict')
    journal.finish(pid,OWNER,token)
    refresh_chapter_read_cache(domain,project_id=pid,chapter_id=cid,user_id=OWNER)
    row=next(row for row in docs.query_collection(('projects',pid,'blocks')) if row.data['position']==0)
    docs.run_transaction(lambda tx:tx.set(row.path,{**row.data,field:int(row.data[field])+1}))
    with pytest.raises(DocumentConflict,match='RECORD_CHANGED'):journal.restore(pid,OWNER,token,'undo')


def test_resumable_steps_are_bounded_and_do_not_change_history_snapshots(monkeypatch):
    import hosted.http_api as http
    from tests.test_v10552 import legacy_project
    one,_,docs,pid,cid=legacy_project(monkeypatch,150);monkeypatch.setattr(http,'runtime',one)
    # Place all150 paragraphs in one chapter to exercise three resumable steps.
    docs.run_transaction(lambda tx:[tx.set(row.path,{**row.data,'chapter_id':cid,'position':index})
        for index,row in enumerate(tx.query_collection(('projects',pid,'blocks')))])
    before=http._project_snapshot(pid)
    totals=[]
    for _ in range(3):
        result=refresh_chapter_read_cache(one.projects.domain,project_id=pid,chapter_id=cid,user_id=OWNER)
        totals.append(result['updated'])
    assert totals==[64,64,22] and not result['more']
    assert refresh_chapter_read_cache(one.projects.domain,project_id=pid,chapter_id=cid,user_id=OWNER)=={'updated':0,'more':False}
    assert http._project_snapshot(pid)==before


def test_insertions_compact_fragmented_windows_without_manual_rebuild(monkeypatch):
    one,make,_,pid,cid=imported(monkeypatch);domain=one.projects.domain
    for index in range(8):
        rows=domain.list('manuscript_block',{'project_id':pid},user_id=OWNER)
        blocks=[{'text_content':f'Synthetic inserted {index}','content_html':f'Synthetic inserted {index}'}]
        blocks.extend({key:row[key] for key in ('id','text_content','content_html')} for row in rows)
        replace_chapter_blocks(domain,project_id=pid,chapter_id=cid,user_id=OWNER,blocks=blocks,
            expected_revision=domain.project_generation(pid,OWNER),operation=f'op_cache_insert_{index}')
        for step in range(3):
            result=refresh_chapter_read_cache(domain,project_id=pid,chapter_id=cid,user_id=OWNER)
            assert result['updated']<=64
            if not result['more']:break
        assert not result['more'],'Incremental compaction failed to converge within the chapter windows'
        hints=set();reader=make().projects.domain;reads=[];native=reader.objects.read_chunks
        with monkeypatch.context() as scope:
            def counted(*args,**kwargs):reads.append(args[0]);return native(*args,**kwargs)
            scope.setattr(reader.objects,'read_chunks',counted)
            actual=reader.list('manuscript_block',{'project_id':pid},user_id=OWNER,cache_refresh_chapters=hints)
        assert len(reads)<=6 and not hints
        assert len(actual)==131+index and actual[0]['text_content']==f'Synthetic inserted {index}'
        assert {f'Synthetic paragraph {i}' for i in range(130)}.issubset({row['text_content'] for row in actual})
