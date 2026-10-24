"""Legacy acceleration retains canonical data and never wins over newer edits."""
from copy import deepcopy

import pytest

from hosted.firestore.contracts import ProjectUnavailable
from hosted.manuscript_read_cache import build_legacy_read_cache
from hosted.runtime_services import _DEV_USER_ID as OWNER
from tests.test_v10552 import legacy_project


def test_verified_bundles_reduce_legacy_reads_without_content_or_generation_changes(monkeypatch):
    one,make,docs,pid,_=legacy_project(monkeypatch,600);domain=one.projects.domain
    originals=docs.query_collection(('projects',pid,'blocks'));before=deepcopy(docs.get(('projects',pid)).data)
    ids=[row.path[-1] for row in originals];counts=[]
    for offset in range(0,len(ids),64):
        counts.append(build_legacy_read_cache(domain,project_id=pid,user_id=OWNER,block_ids=ids[offset:offset+64])['cached'])
    assert sum(counts)==600 and docs.get(('projects',pid)).data==before
    for row in originals:
        saved=dict(docs.get(row.path).data);saved.pop('_bm_read_bundle')
        assert saved==row.data and domain.objects.stat(row.data['payload_object']['object_name']) is not None
    # Rebuild runtime to prove no process-memory cache is required.
    restored=make().projects.domain;reads=[];native=restored.objects.read_chunks
    def counted(*args,**kwargs):reads.append(args[0]);return native(*args,**kwargs)
    monkeypatch.setattr(restored.objects,'read_chunks',counted)
    rows=restored.list('manuscript_block',{'project_id':pid},user_id=OWNER)
    assert len(rows)==600 and len(reads)==10
    assert all(row['text_content']==rows[0]['text_content'] for row in rows)
    # Resume the same bounded batch: no new objects or metadata writes needed.
    reads.clear()
    assert build_legacy_read_cache(restored,project_id=pid,user_id=OWNER,block_ids=ids[:64])['cached']==0
    assert reads==[] and docs.get(('projects',pid)).data==before
    with pytest.raises(ProjectUnavailable):
        build_legacy_read_cache(restored,project_id=pid,user_id='usr_'+'f'*32,block_ids=ids[:64])
    assert reads==[]


def test_newer_edit_wins_and_future_edits_invalidate_derived_cache(monkeypatch):
    one,make,docs,pid,_=legacy_project(monkeypatch,3);domain=one.projects.domain
    originals=docs.query_collection(('projects',pid,'blocks'));ids=[row.path[-1] for row in originals]
    native=domain._publish_hybrid;intervened=[]
    def concurrent(*args,**kwargs):
        pointer=native(*args,**kwargs)
        if not intervened:
            intervened.append(True)
            domain.update('manuscript_block',{'project_id':pid,'block_id':ids[0]},
                {'text_content':'Concurrent synthetic edit','content_html':'Concurrent synthetic edit'},user_id=OWNER)
        return pointer
    monkeypatch.setattr(domain,'_publish_hybrid',concurrent)
    result=build_legacy_read_cache(domain,project_id=pid,user_id=OWNER,block_ids=ids)
    assert result['cached']==2 and '_bm_read_bundle' not in docs.get(originals[0].path).data
    domain.update('manuscript_block',{'project_id':pid,'block_id':ids[1]},
        {'text_content':'Later synthetic edit','content_html':'Later synthetic edit'},user_id=OWNER)
    rows=make().projects.domain.list('manuscript_block',{'project_id':pid},user_id=OWNER)
    by_id={row['id']:row for row in rows}
    assert by_id[ids[0]]['text_content']=='Concurrent synthetic edit'
    assert by_id[ids[1]]['text_content']=='Later synthetic edit'


def test_corrupt_source_never_attaches_a_read_bundle(monkeypatch):
    one,_,docs,pid,_=legacy_project(monkeypatch,3);rows=docs.query_collection(('projects',pid,'blocks'))
    data=deepcopy(rows[0].data);data['payload_object']['object_sha256']='f'*64
    docs.run_transaction(lambda tx:tx.set(rows[0].path,data))
    with pytest.raises(RuntimeError,match='HASH_MISMATCH'):
        build_legacy_read_cache(one.projects.domain,project_id=pid,user_id=OWNER,block_ids=[row.path[-1] for row in rows])
    assert all('_bm_read_bundle' not in docs.get(row.path).data for row in rows)
