"""Indexed navigation stays small, authorized and chapter-first after caching."""
from contextvars import ContextVar
import threading
import time
from types import SimpleNamespace

import pytest

from hosted.firestore.contracts import DocumentConflict, HostedValidationError, ProjectUnavailable
from hosted.firestore.google_driver import _GoogleTransaction
from hosted.firestore.memory import _MemoryTransaction
from hosted.runtime_services import _DEV_USER_ID as OWNER
from tests.test_v10552 import legacy_project
from tests.test_v10554 import imported


def test_healthy_bundled_large_project_stays_chapter_first_without_enumerating_paragraphs(monkeypatch):
    one,_,docs,pid,_=imported(monkeypatch,count=600)
    original=_MemoryTransaction.query_collection;queries=[]
    def indexed(tx,path,**kwargs):
        if path==('projects',pid,'blocks'):
            assert kwargs.get('field')=='semantic_style','Navigation fetched all paragraph metadata'
            queries.append(kwargs['value'])
        return original(tx,path,**kwargs)
    monkeypatch.setattr(_MemoryTransaction,'query_collection',indexed)
    monkeypatch.setattr(one.projects.objects,'read_chunks',lambda *a,**k:pytest.fail('Ordinary body read during navigation'))
    nav=one.projects.domain.manuscript_navigation(pid,user_id=OWNER)
    assert nav=={'chapter_first':True,'block_count':600,'chapter_outlines':[]}
    assert queries==['heading2','heading3','heading4']
    with pytest.raises(ProjectUnavailable):one.projects.domain.manuscript_navigation(pid,user_id='usr_'+'f'*32)


def test_provider_count_uses_same_transaction_and_validates_collection_path():
    transaction=object();calls=[]
    class Collection:
        def count(self,**kwargs):calls.append(kwargs);return self
        def get(self,**kwargs):calls.append(kwargs);return [[SimpleNamespace(value=5574)]]
    tx=_GoogleTransaction(SimpleNamespace(collection=lambda path:(calls.append(path),Collection())[1]),transaction)
    assert tx.count_collection(('projects','synthetic','blocks'))==5574
    assert calls==['projects/synthetic/blocks',{'alias':'total'},{'transaction':transaction}]
    with pytest.raises(HostedValidationError,match='COLLECTION_PATH_INVALID'):tx.count_collection(('projects','synthetic'))


def test_parallel_bootstrap_preserves_request_context_and_bounds_workers(monkeypatch):
    import hosted.http_api as http
    one,_,_,pid,_=legacy_project(monkeypatch,600)
    monkeypatch.setattr(http,'runtime',one)
    marker=ContextVar('synthetic_request_identity');marker.set('current actor')
    native=one.projects.domain.list;lock=threading.Lock();active=peak=0
    def counted(*args,**kwargs):
        nonlocal active,peak
        assert marker.get()=='current actor'
        with lock:active+=1;peak=max(peak,active)
        try:
            time.sleep(.005)
            return native(*args,**kwargs)
        finally:
            with lock:active-=1
    monkeypatch.setattr(one.projects.domain,'list',counted)
    snapshot=http._project_snapshot(pid,navigation_only=True)
    assert 1<peak<=4 and snapshot['blocks']==[]
    assert snapshot['manuscript_navigation']['block_count']==600
    assert all(ch['revision']==str(snapshot['project']['generation']) for ch in snapshot['chapters'])


def test_parallel_bootstrap_still_rejects_revoked_membership_and_generation_race(monkeypatch):
    import hosted.http_api as http
    one,_,_,pid,_=legacy_project(monkeypatch,600)
    monkeypatch.setattr(http,'runtime',one)
    native=one.projects.domain.manuscript_navigation
    def race(*args,**kwargs):
        nav=native(*args,**kwargs);one.projects.rename(pid,'Synthetic concurrent change');return nav
    monkeypatch.setattr(one.projects.domain,'manuscript_navigation',race)
    with pytest.raises(DocumentConflict,match='SNAPSHOT_GENERATION_CHANGED'):http._project_snapshot(pid,navigation_only=True)
    monkeypatch.setattr(http,'_uid',lambda:'usr_'+'f'*32)
    with pytest.raises(ProjectUnavailable):http._project_snapshot(pid,navigation_only=True)


def test_small_bundled_project_count_and_empty_outline_remain_correct(monkeypatch):
    one,_,_,pid,_=imported(monkeypatch,count=2)
    assert one.projects.domain.manuscript_navigation(pid,user_id=OWNER)=={
        'chapter_first':False,'block_count':2,'chapter_outlines':[]}


def test_outline_reads_shared_verified_bundle_only_once(monkeypatch):
    one,_,docs,pid,_=imported(monkeypatch,count=600)
    rows=sorted(docs.query_collection(('projects',pid,'blocks')),key=lambda r:r.data['position'])[:2]
    docs.run_transaction(lambda tx:[tx.set(row.path,{**row.data,'semantic_style':'heading2'}) for row in rows])
    native=one.projects.objects.read_chunks;reads=[]
    def counted(*args,**kwargs):reads.append(args[0]);return native(*args,**kwargs)
    monkeypatch.setattr(one.projects.objects,'read_chunks',counted)
    nav=one.projects.domain.manuscript_navigation(pid,user_id=OWNER)
    assert len(nav['chapter_outlines'])==2 and len(reads)==1
    assert all(row['text_content'].startswith('Synthetic paragraph') for row in nav['chapter_outlines'])
