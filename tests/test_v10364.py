"""M8B cross-replica chapter replacement and lifecycle contracts.

Run only via the user-operated Breadmaker verifier. No live GCP or Playwright.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'src'
if str(SRC) not in sys.path:sys.path.insert(0,str(SRC))

from hosted.chapter_mutations import replace_chapter_blocks, MAX_REPLACEMENT_BLOCKS
from hosted.firestore.contracts import DocumentConflict, HostedValidationError
from hosted.firestore.memory import MemoryDocumentStore
from hosted.storage.memory import MemoryObjectStore
from hosted.runtime_services import _DEV_USER_ID
from runtime.hosted import build_hosted_dev_runtime


def _replicas(monkeypatch):
    monkeypatch.setenv('BREADMAKER_AUTH_MODE','developer')
    monkeypatch.setenv('BREADMAKER_ENV','development')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED','1')
    docs=MemoryDocumentStore()
    objects=MemoryObjectStore()
    def make():return build_hosted_dev_runtime(documents=docs,objects=objects)
    one=make();pid=one.projects.create('M8B test')['id']
    chapter=one.projects.domain.list('chapter',{'project_id':pid},user_id=_DEV_USER_ID)[0]
    return one,make,docs,pid,chapter['id']


def _replace(instance,pid,cid,rows,op,rev=None):
    return replace_chapter_blocks(instance.projects.domain,project_id=pid,chapter_id=cid,
        user_id=_DEV_USER_ID,blocks=rows,operation=op,expected_revision=rev)


def _rows(first,pid,cid):
    return first.projects.domain.list('manuscript_block',{'project_id':pid},user_id=_DEV_USER_ID,
        field='chapter_id',value=cid)


def test_v10364_chapter_replace_commits_once_across_reconstructed_instances(monkeypatch):
    one,make,_docs,pid,cid=_replicas(monkeypatch)
    original=_rows(one,pid,cid)[0]
    before=one.projects.get(pid)['generation']
    payload=[{'id':original['id'],'content_html':'<p>Changed</p>','text_content':'Changed'},
             {'content_html':'<p>Second</p>','text_content':'Second'}]
    first=_replace(one,pid,cid,payload,'op_m8b_replace',before)
    assert first['generation']==before+1
    assert [row['text_content'] for row in _rows(make(),pid,cid)]==['Changed','Second']
    replay=_replace(make(),pid,cid,payload,'op_m8b_replace',before)
    assert replay['ids']==first['ids']
    assert make().projects.get(pid)['generation']==before+1
    assert len(_rows(make(),pid,cid))==2


def test_v10364_reusing_key_with_altered_payload_conflicts(monkeypatch):
    one,make,_docs,pid,cid=_replicas(monkeypatch)
    prior=one.projects.get(pid)['generation']
    _replace(one,pid,cid,[{'text_content':'one'}],'op_m8b_same_key',prior)
    with pytest.raises(DocumentConflict,match='HOSTED_IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD'):
        _replace(make(),pid,cid,[{'text_content':'two'}],'op_m8b_same_key',prior)
    assert [r['text_content'] for r in _rows(make(),pid,cid)]==['one']


def test_v10364_stale_writer_cannot_overwrite_committed_blocks(monkeypatch):
    one,make,_docs,pid,cid=_replicas(monkeypatch)
    before=one.projects.get(pid)['generation']
    _replace(one,pid,cid,[{'text_content':'one'}],'op_m8b_first',before)
    with pytest.raises(DocumentConflict,match='HOSTED_GENERATION_CONFLICT'):
        _replace(make(),pid,cid,[{'text_content':'stale'}],'op_m8b_stale',before)
    assert [r['text_content'] for r in _rows(make(),pid,cid)]==['one']


def test_v10364_transaction_callback_retry_does_not_duplicate_generation(monkeypatch):
    one,make,docs,pid,cid=_replicas(monkeypatch)
    before=one.projects.get(pid)['generation']
    native=docs.run_transaction
    def inject_retry(fn,**kwargs):
        if fn.__name__=='mutate':docs.retry_next_transaction()
        return native(fn,**kwargs)
    monkeypatch.setattr(docs,'run_transaction',inject_retry)
    result=_replace(one,pid,cid,[{'text_content':'retained'}],'op_m8b_provider_retry',before)
    assert result['generation']==before+1
    assert make().projects.get(pid)['generation']==before+1
    assert len(_rows(make(),pid,cid))==1


def test_v10364_preflight_rejects_invalid_duplicate_or_oversized_batch_without_writes(monkeypatch):
    one,make,_docs,pid,cid=_replicas(monkeypatch)
    first=_rows(one,pid,cid)[0]
    before=one.projects.get(pid)['generation']
    for incoming in ([{'id':first['id']},{'id':first['id']}],
                     [{'chapter_id':'chapter_foreign'}],
                     [{'text_content':'x'}]*(MAX_REPLACEMENT_BLOCKS+1),
                     [{'generation':0}]):
        with pytest.raises(HostedValidationError):
            _replace(one,pid,cid,incoming,'op_m8b_bad_'+str(len(incoming)),before)
    assert make().projects.get(pid)['generation']==before
    assert _rows(make(),pid,cid)[0]['id']==first['id']


def test_v10364_failed_content_staging_cannot_publish_partial_chapter(monkeypatch):
    one,make,_docs,pid,cid=_replicas(monkeypatch)
    prior=one.projects.get(pid)['generation']
    before=[r['text_content'] for r in _rows(one,pid,cid)]
    domain=one.projects.domain
    original_publish=domain._publish_hybrid
    calls=[0]
    def interrupt(*args,**kwargs):
        calls[0]+=1
        if calls[0]==2:raise RuntimeError('INTERRUPTED_GCS_STAGE')
        return original_publish(*args,**kwargs)
    monkeypatch.setattr(domain,'_publish_hybrid',interrupt)
    with pytest.raises(RuntimeError,match='INTERRUPTED_GCS_STAGE'):
        _replace(one,pid,cid,[{'text_content':'One'},{'text_content':'Two'}],'op_m8b_interrupt',prior)
    assert make().projects.get(pid)['generation']==prior
    assert [r['text_content'] for r in _rows(make(),pid,cid)]==before
    # Same operation can safely complete on a new instance after interruption.
    result=_replace(make(),pid,cid,[{'text_content':'One'},{'text_content':'Two'}],'op_m8b_interrupt',prior)
    assert result['generation']==prior+1


def test_v10364_replay_after_subsequent_commit_is_rejected_not_reapplied(monkeypatch):
    one,make,_docs,pid,cid=_replicas(monkeypatch)
    initial=one.projects.get(pid)['generation']
    payload=[{'text_content':'first'}]
    _replace(one,pid,cid,payload,'op_m8b_old',initial)
    second=make().projects.get(pid)['generation']
    _replace(make(),pid,cid,[{'text_content':'second'}],'op_m8b_new',second)
    with pytest.raises(DocumentConflict,match='HOSTED_REPLAY_SUPERSEDED'):
        _replace(make(),pid,cid,payload,'op_m8b_old',initial)
    assert [r['text_content'] for r in _rows(make(),pid,cid)]==['second']


def test_v10364_old_chapter_mutation_is_not_a_per_block_loop(monkeypatch):
    import asyncio
    import json
    from fastapi import HTTPException
    from starlette.requests import Request
    import hosted.chapter_mutations as mutations
    import hosted.http_api as http

    one,make,_,pid,cid=_replicas(monkeypatch)
    monkeypatch.setattr(http,'runtime',one)
    calls=[];native=mutations.replace_chapter_blocks
    def delegated(domain,**kwargs):
        calls.append(kwargs)
        return native(domain,**kwargs)
    def individual_write(*args,**kwargs):
        pytest.fail('Chapter HTTP save must not publish individual CRUD writes')
    monkeypatch.setattr(mutations,'replace_chapter_blocks',delegated)
    monkeypatch.setattr(http,'_create',individual_write)
    monkeypatch.setattr(http,'_update',individual_write)
    monkeypatch.setattr(http,'_delete',individual_write)
    prior=one.projects.get(pid)['generation']
    blocks=[{'text_content':f'Synthetic paragraph {index}','content_html':f'Synthetic paragraph {index}'} for index in range(20)]
    def request(revision):
        async def receive():return {'type':'http.request','body':json.dumps(
            {'blocks':blocks,'expected_revision':revision}).encode(),'more_body':False}
        return Request({'type':'http','method':'PUT','path':f'/api/chapters/{cid}/blocks',
            'query_string':b'','headers':[(b'x-breadmaker-project',pid.encode())]},receive)
    result=asyncio.run(http.hosted_chapter_blocks(cid,request(prior)))
    assert len(calls)==1 and calls[0]['project_id']==pid and calls[0]['chapter_id']==cid
    assert calls[0]['user_id']==_DEV_USER_ID and calls[0]['blocks']==blocks
    assert result['generation']==prior+1 and make().projects.get(pid)['generation']==prior+1
    assert [row['text_content'] for row in _rows(make(),pid,cid)]==[row['text_content'] for row in blocks]
    with pytest.raises(HTTPException) as stale:
        asyncio.run(http.hosted_chapter_blocks(cid,request(prior)))
    assert stale.value.status_code==409 and len(calls)==2
    assert make().projects.get(pid)['generation']==prior+1
    assert [row['text_content'] for row in _rows(make(),pid,cid)]==[row['text_content'] for row in blocks]
    lifecycle=(ROOT/'docs/HOSTED_CLOUD_RUN_LIFECYCLE_M8B.md').read_text()
    assert 'Cloud Run' in lifecycle and 'SIGTERM' in lifecycle and 'no sticky session' in lifecycle
    assert 'whole-request replay' in lifecycle
