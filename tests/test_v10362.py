from __future__ import annotations

"""M8 replica-loss contracts; deliberately use shared memory Firestore/GCS fakes.

The assistant must never execute this suite. The release operator runs it via the
regular Breadmaker verifier. No cloud credentials, browser, or network are needed.
"""

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'src'
if str(SRC) not in sys.path:sys.path.insert(0,str(SRC))

from hosted.firestore.memory import MemoryDocumentStore
from hosted.firestore.contracts import DocumentConflict
from hosted.storage.memory import MemoryObjectStore
from hosted.idempotency import bind_operations, operation_for, request_scope_key, stable_entity_id, stable_project_id
from hosted.runtime_services import _DEV_USER_ID
from hosted.packages import HostedProjectPackageService
from runtime.hosted import build_hosted_dev_runtime


def replicas(monkeypatch):
    monkeypatch.setenv('BREADMAKER_AUTH_MODE','developer')
    monkeypatch.setenv('BREADMAKER_ENV','development')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED','1')
    monkeypatch.setenv('WEB_CONCURRENCY','4')
    documents=MemoryDocumentStore()
    objects=MemoryObjectStore()
    make=lambda:build_hosted_dev_runtime(documents=documents,objects=objects)
    return make(),make,documents,objects


def test_v10362_reconstructed_runtime_sees_canonical_commit(monkeypatch):
    first,make,_,_=replicas(monkeypatch)
    operation='op_cross_instance_project'
    project=first.projects.create('Restart-safe manuscript',idempotency_operation=operation)
    second=make()
    assert second.projects.get(project['id'])['id']==project['id']
    assert len(second.projects.domain.list('document_tab',{'project_id':project['id']},user_id=_DEV_USER_ID))==1
    generation=second.projects.get(project['id'])['generation']
    repeated=second.projects.create('Restart-safe manuscript',idempotency_operation=operation)
    assert repeated['id']==project['id']
    assert make().projects.get(project['id'])['generation']==generation


def test_v10362_reused_request_key_cannot_change_project_title(monkeypatch):
    first,make,_,_=replicas(monkeypatch)
    first.projects.create('One',idempotency_operation='op_project_replay')
    with pytest.raises(Exception):make().projects.create('Different',idempotency_operation='op_project_replay')


def test_v10362_request_scope_rejects_ambiguous_keys_and_reconstructs_operations():
    for value in ('','invalid key','a\nInjected: true','comma,key','"quoted"'):
        with pytest.raises(ValueError):request_scope_key(value,actor=_DEV_USER_ID,method='POST',path='/api/tasks')
    one=request_scope_key('client-nonce-1',actor=_DEV_USER_ID,method='POST',path='/api/tasks')
    two=request_scope_key('client-nonce-1',actor=_DEV_USER_ID,method='POST',path='/api/projects')
    assert one!=two
    with bind_operations(one):a=[operation_for('entity_create:task'),operation_for('entity_create:task')]
    with bind_operations(one):b=[operation_for('entity_create:task'),operation_for('entity_create:task')]
    assert a==b and a[0]!=a[1]
    assert stable_project_id(a[0])==stable_project_id(b[0])


def test_v10362_entity_replay_after_new_runtime_preserves_generation(monkeypatch):
    first,make,_,_=replicas(monkeypatch)
    project=first.projects.create('Draft')
    pid=project['id'];op='op_retry_task_1';eid=stable_entity_id('task_',op)
    row=first.projects.domain.create('task',{'project_id':pid},{'title':'Revise chapter'},user_id=_DEV_USER_ID,entity_id=eid,operation=op)
    after_create=make().projects.get(pid)['generation']
    replay=make().projects.domain.create('task',{'project_id':pid},{'title':'Revise chapter'},user_id=_DEV_USER_ID,entity_id=eid,operation=op)
    assert replay['id']==row['id']
    assert make().projects.get(pid)['generation']==after_create
    with pytest.raises(Exception):make().projects.domain.create('task',{'project_id':pid},{'title':'Different'},user_id=_DEV_USER_ID,entity_id=eid,operation=op)


def test_v10362_cross_replica_generation_cas_rejects_stale_writer(monkeypatch):
    first,make,_,_=replicas(monkeypatch)
    pid=first.projects.create('Multiwriter')['id']
    document=first.projects.domain.create('task',{'project_id':pid},{'title':'Original'},user_id=_DEV_USER_ID)
    eid=document['id'];generation=document['generation']
    second=make()
    first.projects.domain.update('task',{'project_id':pid,'task_id':eid},{'title':'First'},user_id=_DEV_USER_ID,expected_generation=generation)
    with pytest.raises(Exception):second.projects.domain.update('task',{'project_id':pid,'task_id':eid},{'title':'Stale'},user_id=_DEV_USER_ID,expected_generation=generation)
    assert make().projects.domain.get('task',{'project_id':pid,'task_id':eid},user_id=_DEV_USER_ID)['title']=='First'


def test_v10362_annotation_batch_replay_is_single_transaction(monkeypatch):
    first,make,_,_=replicas(monkeypatch)
    pid=first.projects.create('Batch')['id']
    block=first.projects.domain.list('manuscript_block',{'project_id':pid},user_id=_DEV_USER_ID)[0]
    payload=[{'block_id':block['id'],'annotation_type':'highlight','start_offset':0,'end_offset':2,'selected_text':'He'},
             {'block_id':block['id'],'annotation_type':'note','start_offset':2,'end_offset':5,'selected_text':'llo'}]
    op='op_batch_once'
    initial=first.projects.domain.create_batch_atomic('annotation',{'project_id':pid},payload,user_id=_DEV_USER_ID,operation=op)
    generation=make().projects.get(pid)['generation']
    again=make().projects.domain.create_batch_atomic('annotation',{'project_id':pid},payload,user_id=_DEV_USER_ID,operation=op)
    assert [x['id'] for x in initial]==[x['id'] for x in again]
    assert make().projects.get(pid)['generation']==generation
    with pytest.raises(Exception):make().projects.domain.create_batch_atomic('annotation',{'project_id':pid},[dict(payload[0],selected_text='Changed'),payload[1]],user_id=_DEV_USER_ID,operation=op)


def test_v10362_import_replay_receipt_survives_new_runtime(monkeypatch):
    first,make,_,_=replicas(monkeypatch)
    content=io.BytesIO()
    with zipfile.ZipFile(content,'w') as archive:
        archive.writestr('manifest.json',json.dumps({'format':'breadmaker-hosted-project','format_version':3,'title':'Portable'}))
        archive.writestr('project.json',json.dumps({'entities':{}}))
    payload=content.getvalue()
    op='op_package_retry'
    initial=HostedProjectPackageService(first.projects).import_bytes(payload,_DEV_USER_ID,operation=op)
    again=HostedProjectPackageService(make().projects).import_bytes(payload,_DEV_USER_ID,operation=op)
    assert initial['id']==again['id']
    assert make().projects.get(initial['id'])['id']==initial['id']
    alternate=io.BytesIO()
    with zipfile.ZipFile(alternate,'w') as archive:
        archive.writestr('manifest.json',json.dumps({'format':'breadmaker-hosted-project','format_version':3,'title':'Different'}))
        archive.writestr('project.json',json.dumps({'entities':{}}))
    with pytest.raises(DocumentConflict,match='HOSTED_IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PACKAGE'):
        HostedProjectPackageService(make().projects).import_bytes(alternate.getvalue(),_DEV_USER_ID,operation=op)


def test_v10362_hosted_production_never_accepts_memory_store_injection(monkeypatch):
    import runtime.hosted as module
    docs=MemoryDocumentStore();objects=MemoryObjectStore()
    with pytest.raises(RuntimeError,match='HOSTED_STORE_INJECTION_DEVELOPMENT_ONLY'):
        module._stores('hosted',docs,objects)


def test_v10362_runtime_removes_process_lock_and_legacy_volume():
    hosted=(SRC/'runtime/hosted.py').read_text()
    docker=(ROOT/'deploy/Dockerfile.hosted').read_text()
    assert 'HOSTED_SINGLE_WORKER_REQUIRED' not in hosted
    assert "'single_worker_required':False" in hosted
    assert 'VOLUME ["/data"]' not in docker and 'BREADMAKER_HOME=/data' not in docker
    assert 'USER 10001:10001' in docker
