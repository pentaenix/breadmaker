"""Real hosted seams: cross-replica journal, safe compensation and GCS replay."""
from copy import deepcopy
from types import SimpleNamespace
import sys

import pytest

from hosted.action_journal import ActionJournal, bind_action, MAX_RECORDS
from hosted.firestore.contracts import DocumentConflict, DocumentNotFound, HostedValidationError
from hosted.runtime_services import _DEV_USER_ID as OWNER
from tests.test_v10364 import _replicas, _replace


def create_canvas(instance, pid, token, name='Synthetic canvas'):
    with bind_action(token, pid, OWNER):
        return instance.projects.domain.create('canvas', {'project_id':pid},
            {'name':name}, user_id=OWNER, operation='canvas_'+name.replace(' ','_'))


def test_action_finish_undo_redo_and_replay_survive_new_instances(monkeypatch):
    one, make, docs, pid, _ = _replicas(monkeypatch)
    token = ActionJournal(one.projects.documents).begin(pid, OWNER)['token']
    docs.retry_next_transaction()
    canvas = create_canvas(make(), pid, token)
    assert ActionJournal(make().projects.documents).finish(pid, OWNER, token)['changed']
    journal = ActionJournal(make().projects.documents)
    assert journal.restore(pid, OWNER, token, 'undo')['changed']
    assert not one.projects.domain.list('canvas', {'project_id':pid}, user_id=OWNER)
    assert not journal.restore(pid, OWNER, token, 'undo')['changed']
    assert journal.restore(pid, OWNER, token, 'redo')['changed']
    rows = make().projects.domain.list('canvas', {'project_id':pid}, user_id=OWNER)
    assert rows[0]['id'] == canvas['id'] and rows[0]['name'] == 'Synthetic canvas'
    assert rows[0]['generation'] > canvas['generation']
    journal.restore(pid, OWNER, token, 'undo')
    journal.restore(pid, OWNER, token, 'redo')
    assert make().projects.domain.list('canvas', {'project_id':pid}, user_id=OWNER)[0]['generation']>rows[0]['generation']


def test_failed_multistep_action_compensates_only_its_committed_writes(monkeypatch):
    one, make, docs, pid, _ = _replicas(monkeypatch)
    journal = ActionJournal(docs)
    token = journal.begin(pid, OWNER)['token']
    create_canvas(one, pid, token)
    # Simulated later step failure/process loss: a new replica restores journal.
    assert ActionJournal(make().projects.documents).restore(pid, OWNER, token, 'rollback')['changed']
    assert not one.projects.domain.list('canvas', {'project_id':pid}, user_id=OWNER)
    assert not journal.restore(pid, OWNER, token, 'rollback')['changed']


def test_consecutive_actions_undo_and_redo_without_generation_reuse(monkeypatch):
    one,make,docs,pid,_=_replicas(monkeypatch)
    journal=ActionJournal(docs)
    first=journal.begin(pid,OWNER)['token']
    canvas=create_canvas(one,pid,first)
    journal.finish(pid,OWNER,first)
    second=journal.begin(pid,OWNER)['token']
    with bind_action(second,pid,OWNER):
        make().projects.domain.update('canvas',{'project_id':pid,'canvas_id':canvas['id']},
            {'name':'Renamed synthetic'},user_id=OWNER,operation='synthetic_rename')
    journal.finish(pid,OWNER,second)
    for token in (second,first):ActionJournal(make().projects.documents).restore(pid,OWNER,token,'undo')
    assert not one.projects.domain.list('canvas',{'project_id':pid},user_id=OWNER)
    for token in (first,second):ActionJournal(make().projects.documents).restore(pid,OWNER,token,'redo')
    assert one.projects.domain.list('canvas',{'project_id':pid},user_id=OWNER)[0]['name']=='Renamed synthetic'


def test_intervening_write_prevents_action_finish_or_compensation(monkeypatch):
    one, make, docs, pid, cid = _replicas(monkeypatch)
    token = ActionJournal(docs).begin(pid, OWNER)['token']
    create_canvas(one, pid, token)
    _replace(make(), pid, cid, [{'text_content':'Keep concurrent text'}], 'other_writer')
    for operation in ('finish', 'rollback'):
        with pytest.raises(DocumentConflict, match='STALE'):
            journal = ActionJournal(docs)
            if operation == 'finish': journal.finish(pid, OWNER, token)
            else: journal.restore(pid, OWNER, token, operation)
    assert one.projects.domain.list('canvas', {'project_id':pid}, user_id=OWNER)
    assert one.projects.domain.list('manuscript_block', {'project_id':pid}, user_id=OWNER)[0]['text_content']=='Keep concurrent text'


def test_token_is_bound_to_actor_project_and_current_membership(monkeypatch):
    one, _, docs, pid, _ = _replicas(monkeypatch)
    journal = ActionJournal(docs)
    token = journal.begin(pid, OWNER)['token']
    other = one.projects.create('Other synthetic project')['id']
    with pytest.raises(DocumentNotFound): journal.finish(other, OWNER, token)
    docs.run_transaction(lambda tx:tx.delete(('projects',pid,'members',OWNER)))
    from hosted.firestore.contracts import ProjectUnavailable
    with pytest.raises(ProjectUnavailable): journal.restore(pid, OWNER, token, 'rollback')


def test_action_cannot_journal_or_restore_permission_records(monkeypatch):
    one, _, docs, pid, _ = _replicas(monkeypatch)
    token = ActionJournal(docs).begin(pid, OWNER)['token']
    path = ('projects',pid,'members',OWNER)
    before = deepcopy(docs.get(path).data)
    with bind_action(token, pid, OWNER), pytest.raises(HostedValidationError, match='UNSUPPORTED'):
        one.projects.documents.run_transaction(lambda tx:tx.set(path, {'role':'editor'}))
    assert docs.get(path).data == before


def test_action_limit_rejects_entire_transaction_before_any_write(monkeypatch):
    one, _, docs, pid, _ = _replicas(monkeypatch)
    token = ActionJournal(docs).begin(pid, OWNER)['token']
    def too_large(tx):
        for n in range(MAX_RECORDS+1):tx.create(('projects',pid,'canvases',f'c_{n}'),{'name':'Synthetic'})
    with bind_action(token, pid, OWNER), pytest.raises(HostedValidationError, match='TOO_LARGE'):
        one.projects.documents.run_transaction(too_large)
    assert docs.query_collection(('projects',pid,'canvases')) == []


def test_gcs_resumable_412_is_safe_existing_object_not_generic_server_failure(monkeypatch):
    # Match the pinned SDK's exception interface without importing cloud SDKs
    # into the local/offline Editor test dependency graph.
    class InvalidResponse(Exception):
        def __init__(self, response, message):
            super().__init__(message)
            self.response=response
    class NotFound(Exception): pass
    class PreconditionFailed(Exception): pass
    class DataCorruption(Exception): pass
    monkeypatch.setitem(sys.modules, 'google.api_core.exceptions', SimpleNamespace(
        NotFound=NotFound, PreconditionFailed=PreconditionFailed))
    monkeypatch.setitem(sys.modules, 'google.cloud.storage.exceptions', SimpleNamespace(
        InvalidResponse=InvalidResponse, DataCorruption=DataCorruption))
    from hosted.storage.google_driver import GoogleCloudObjectStore
    from hosted.storage.contracts import ObjectGenerationConflict, ObjectAlreadyExists
    error = InvalidResponse(SimpleNamespace(status_code=412), 'Synthetic HTTP412')
    assert isinstance(GoogleCloudObjectStore._translate(error), ObjectGenerationConflict)
    blob = SimpleNamespace(metadata={}, upload_from_file=lambda *args,**kwargs: (_ for _ in ()).throw(error))
    client = SimpleNamespace(bucket=lambda name: SimpleNamespace(blob=lambda key:blob))
    store = GoogleCloudObjectStore(bucket='synthetic',client=client)
    with pytest.raises(ObjectAlreadyExists):
        store.write_chunks('projects/synthetic/test.json',[b'unchanged'],max_bytes=100,
            content_type='application/json',if_generation_match=0)
    with pytest.raises(ObjectGenerationConflict):
        store.write_chunks('projects/synthetic/test.json',[b'unchanged'],max_bytes=100,
            content_type='application/json',if_generation_match=123)
    forbidden = InvalidResponse(SimpleNamespace(status_code=403), 'Synthetic HTTP403')
    assert GoogleCloudObjectStore._translate(forbidden) is forbidden


def test_provider_reads_all_precede_atomic_journal_and_product_writes(monkeypatch):
    one, make, docs, pid, _ = _replicas(monkeypatch)
    original=docs.run_transaction
    class StrictTransaction:
        def __init__(self,tx):self.tx,self.written=tx,False
        def get(self,path):
            assert not self.written,'Firestore forbids reads after writes'
            return self.tx.get(path)
        def query_collection(self,path,**kwargs):
            assert not self.written,'Firestore forbids reads after writes'
            return self.tx.query_collection(path,**kwargs)
        def create(self,path,data):self.written=True;return self.tx.create(path,data)
        def set(self,path,data):self.written=True;return self.tx.set(path,data)
        def delete(self,path):self.written=True;return self.tx.delete(path)
    monkeypatch.setattr(docs,'run_transaction',lambda fn,**kw:original(lambda tx:fn(StrictTransaction(tx)),**kw))
    journal=ActionJournal(docs)
    token=journal.begin(pid,OWNER)['token']
    create_canvas(make(),pid,token)
    journal.finish(pid,OWNER,token)
    journal.restore(pid,OWNER,token,'undo')


def test_history_expiry_pruning_and_actor_mismatch_are_fail_closed(monkeypatch):
    one, _, docs, pid, _ = _replicas(monkeypatch)
    import hosted.action_journal as actions
    journal=ActionJournal(docs)
    token=journal.begin(pid,OWNER)['token']
    from hosted.firestore.contracts import ProjectUnavailable
    with pytest.raises(ProjectUnavailable):
        journal.finish(pid,'usr_'+'f'*32,token)
    monkeypatch.setattr(actions.time,'time',lambda:10**12)
    with pytest.raises(DocumentConflict,match='EXPIRED'):
        journal.finish(pid,OWNER,token)
    journal.begin(pid,OWNER)
    assert docs.get(('hosted_actions',OWNER,'tokens',token)) is None


def test_shell_title_is_readable_before_any_translation_loads(client):
    for route in ('/','/canvas','/maps','/tasks','/reviews'):
        response=client.get(route)
        assert response.status_code==200
        assert '<title>Breadmaker Studio</title>' in response.text
        assert '<title>{{i18n:' not in response.text
        assert response.headers['cache-control']=='no-store, max-age=0'


def test_sandbox_unwraps_only_known_journal_facade_and_still_rejects_cloud_provider(monkeypatch):
    from hosted.action_journal import ActionDocumentStore
    from hosted.firestore.google_driver import GoogleFirestoreDocumentStore
    from hosted.purge_discovery import MemoryProjectTreeDiscovery
    from hosted.purge_planning import HostedPurgePlanner
    from hosted.purge_receipts import HostedPurgePreparationService
    from hosted.purge_sandbox import HostedSandboxPurgeExecutor
    from hosted.storage.memory import MemoryObjectStore
    one,_,docs,_,_=_replicas(monkeypatch)
    discovery=MemoryProjectTreeDiscovery(docs)
    planner=HostedPurgePlanner(one.projects,MemoryObjectStore(),discovery=discovery)
    assert planner.documents is docs
    HostedSandboxPurgeExecutor(HostedPurgePreparationService(planner))
    # Constructing an SDK boundary with an inert injected client performs no I/O.
    google=GoogleFirestoreDocumentStore(client=object())
    one.projects.documents=ActionDocumentStore(google)
    planner=HostedPurgePlanner(one.projects,MemoryObjectStore(),discovery=discovery)
    assert planner.documents is google
    with pytest.raises(ValueError,match='MEMORY_ONLY'):
        HostedSandboxPurgeExecutor(HostedPurgePreparationService(planner))
