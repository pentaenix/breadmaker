"""M12E: opt-in private Firestore Task/milestone storage. Human runs suites only."""
from __future__ import annotations

import copy
import json
import secrets
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))

from hosted.firestore import (
    HostedProjectRepository, HostedStructuredRepository, HostedUserRepository,
    MemoryDocumentStore, ProjectUnavailable,
)
from hosted.firestore.contracts import DocumentAlreadyExists, HostedValidationError
from hosted.runtime_services import HostedCloudProjectRepository, ensure_hosted_user
from hosted.security.envelope import HostedEnvelopeCodec
from hosted.security.private_firestore import HostedPrivateFirestoreFields, PRIVATE_FIELD_NAME
from hosted.storage.memory import MemoryObjectStore
from hosted.packages import HostedProjectPackageService

OWNER = 'usr_' + 'a' * 32
OUTSIDER = 'usr_' + 'b' * 32
RESOURCE = 'projects/breadmaker-test-123/locations/us-central1/keyRings/hosted/cryptoKeys/data'
SECRET = 'Secret task: revise unpublished chapter seven'


class FakeKms:
    key_resource = RESOURCE

    def __init__(self):
        self.key = AESGCM.generate_key(bit_length=256)
        self.available = True

    def wrap(self, raw):
        if not self.available:
            raise RuntimeError('unavailable')
        nonce = secrets.token_bytes(12)
        return nonce + AESGCM(self.key).encrypt(nonce, raw, b'wrap')

    def unwrap(self, wrapped):
        if not self.available:
            raise RuntimeError('unavailable')
        return AESGCM(self.key).decrypt(wrapped[:12], wrapped[12:], b'wrap')


def setup_private_fields():
    docs, objects = MemoryDocumentStore(), MemoryObjectStore()
    ensure_hosted_user(docs, user_id=OWNER, display_name='Writer')
    ensure_hosted_user(docs, user_id=OUTSIDER, display_name='Stranger')
    authority = HostedProjectRepository(docs)
    users = HostedUserRepository(HostedStructuredRepository(docs))
    kms = FakeKms()
    private = HostedPrivateFirestoreFields(HostedEnvelopeCodec(kms))
    projects = HostedCloudProjectRepository(docs, objects, authority, users, lambda: OWNER,
                                             encrypted_fields=private)
    pid = projects.create('Private project')['id']
    return docs, objects, authority, users, kms, private, projects, pid


def test_v10513_task_create_get_update_list_and_firestore_no_plaintext():
    docs, objects, authority, users, kms, private, projects, pid = setup_private_fields()
    task = projects.domain.create('task', {'project_id': pid},
        {'title': SECRET, 'notes': {'summary': SECRET}, 'position': 3, 'status': 'open'},
        user_id=OWNER, operation='create_secret_task', entity_id='task_private1')
    assert task['title'] == SECRET and task['notes']['summary'] == SECRET
    stored = docs.get(('projects',pid,'tasks','task_private1')).data
    assert SECRET not in json.dumps(stored)
    assert 'title' not in stored and 'status' not in stored and 'notes' not in stored
    assert set(stored) - {'id','task_id','generation','created_at','updated_at',
        '_bm_create_operation','_bm_create_fingerprint',PRIVATE_FIELD_NAME} == set()
    changed = projects.domain.update('task', {'project_id':pid,'task_id':'task_private1'},
        {'title': 'Second secret title'}, user_id=OWNER, operation='update_private_task')
    assert changed['title'] == 'Second secret title' and changed['notes']['summary'] == SECRET
    assert 'Second secret title' not in json.dumps(docs.get(('projects',pid,'tasks','task_private1')).data)
    replica = HostedCloudProjectRepository(docs, objects, authority, users, lambda: OWNER, encrypted_fields=private)
    assert replica.domain.get('task',{'project_id':pid,'task_id':'task_private1'},user_id=OWNER)['title']=='Second secret title'
    assert len(replica.domain.list('task',{'project_id':pid},user_id=OWNER,field='status',value='open')) == 1
    assert replica.domain.list('task',{'project_id':pid},user_id=OWNER,field='status',value='closed') == []


def test_v10513_nested_milestone_uses_own_authenticated_path():
    docs, _objects, _authority, _users, _kms, _private, projects, pid = setup_private_fields()
    task = projects.domain.create('task', {'project_id':pid}, {'title':'One'}, user_id=OWNER)
    ids = {'project_id':pid,'task_id':task['id']}
    milestone = projects.domain.create('task_milestone', ids,
        {'title':SECRET, 'description':SECRET},user_id=OWNER,entity_id='milestone_secret')
    raw=docs.get(('projects',pid,'tasks',task['id'],'milestones','milestone_secret')).data
    assert SECRET not in json.dumps(raw)
    assert projects.domain.list('task_milestone',ids,user_id=OWNER)[0]['description']==SECRET
    assert milestone['title']==SECRET


def test_v10513_milestone_ciphertext_cannot_move_between_parent_tasks():
    docs, _objects, _authority, _users, _kms, _private, projects, pid = setup_private_fields()
    left=projects.domain.create('task',{'project_id':pid},{'title':'left'},user_id=OWNER)
    right=projects.domain.create('task',{'project_id':pid},{'title':'right'},user_id=OWNER)
    left_ids={'project_id':pid,'task_id':left['id']}
    right_ids={'project_id':pid,'task_id':right['id']}
    projects.domain.create('task_milestone',left_ids,{'title':SECRET},user_id=OWNER,entity_id='milestone_shared')
    projects.domain.create('task_milestone',right_ids,{'title':'not secret'},user_id=OWNER,entity_id='milestone_shared')
    def transplant(tx):
        source=('projects',pid,'tasks',left['id'],'milestones','milestone_shared')
        dest=('projects',pid,'tasks',right['id'],'milestones','milestone_shared')
        row=dict(tx.get(dest).data)
        row[PRIVATE_FIELD_NAME]=copy.deepcopy(tx.get(source).data[PRIVATE_FIELD_NAME])
        tx.set(dest,row)
    docs.run_transaction(transplant)
    with pytest.raises(HostedValidationError,match='HOSTED_PRIVATE_FIRESTORE_DECRYPT_FAILED'):
        projects.domain.get('task_milestone', {**right_ids,'milestone_id':'milestone_shared'},user_id=OWNER)


def test_v10513_different_user_denied_before_kms_decrypt():
    _docs, _objects, _authority, _users, kms, _private, projects, pid = setup_private_fields()
    row=projects.domain.create('task',{'project_id':pid},{'title':SECRET},user_id=OWNER)
    kms.available=False
    with pytest.raises(ProjectUnavailable):
        projects.domain.get('task',{'project_id':pid,'task_id':row['id']},user_id=OUTSIDER)
    with pytest.raises(ProjectUnavailable):
        projects.domain.list('task',{'project_id':pid},user_id=OUTSIDER)
    with pytest.raises(HostedValidationError,match='HOSTED_PRIVATE_FIRESTORE_DECRYPT_FAILED'):
        projects.domain.get('task',{'project_id':pid,'task_id':row['id']},user_id=OWNER)


def test_v10513_ciphertext_swap_and_plaintext_fallback_rejected():
    docs, _objects, _authority, _users, _kms, _private, projects, pid = setup_private_fields()
    a=projects.domain.create('task',{'project_id':pid},{'title':SECRET},user_id=OWNER,entity_id='task_a')
    b=projects.domain.create('task',{'project_id':pid},{'title':'Other'},user_id=OWNER,entity_id='task_b')
    def swap(tx):
        path=('projects',pid,'tasks','task_b')
        row=dict(tx.get(path).data)
        row[PRIVATE_FIELD_NAME]=copy.deepcopy(tx.get(('projects',pid,'tasks','task_a')).data[PRIVATE_FIELD_NAME])
        tx.set(path,row)
    docs.run_transaction(swap)
    with pytest.raises(HostedValidationError,match='HOSTED_PRIVATE_FIRESTORE_DECRYPT_FAILED'):
        projects.domain.get('task',{'project_id':pid,'task_id':'task_b'},user_id=OWNER)
    def plaintext(tx):
        path=('projects',pid,'tasks','task_b')
        row=dict(tx.get(path).data)
        row['title']=SECRET
        tx.set(path,row)
    docs.run_transaction(plaintext)
    with pytest.raises(HostedValidationError,match='HOSTED_PRIVATE_FIRESTORE_PLAINTEXT_FORBIDDEN'):
        projects.domain.get('task',{'project_id':pid,'task_id':'task_b'},user_id=OWNER)


def test_v10513_replay_protects_private_content_and_idempotency():
    docs, _objects, _authority, _users, _kms, _private, projects, pid = setup_private_fields()
    before = projects.domain.project_generation(pid, OWNER)
    args = ('task', {'project_id':pid}, {'title':SECRET})
    first = projects.domain.create(*args,user_id=OWNER,entity_id='task_replay',operation='task_create_replay')
    generation = projects.domain.project_generation(pid, OWNER)
    assert generation==before+1
    replay = projects.domain.create(*args,user_id=OWNER,entity_id='task_replay',operation='task_create_replay')
    assert replay['id']==first['id']
    assert projects.domain.project_generation(pid,OWNER)==generation
    with pytest.raises(DocumentAlreadyExists):
        projects.domain.create('task',{'project_id':pid},{'title':'Tampered'},
            user_id=OWNER,entity_id='task_replay',operation='task_create_replay')
    assert SECRET not in json.dumps(docs.get(('projects',pid,'tasks','task_replay')).data)


def test_v10513_fail_closed_batch_and_portability_until_encrypted_export():
    docs, objects, _authority, _users, _kms, _private, projects, pid = setup_private_fields()
    with pytest.raises(HostedValidationError, match='HOSTED_PRIVATE_FIRESTORE_BATCH_NOT_READY'):
        projects.domain.create_batch_atomic('task',{'project_id':pid},[{'title':SECRET}],user_id=OWNER)
    packages = HostedProjectPackageService(projects)
    with pytest.raises(ValueError,match='HOSTED_ENCRYPTED_PACKAGE_EXPORT_NOT_READY'):
        packages.export_bytes(pid, OWNER)
    with pytest.raises(ValueError,match='HOSTED_ENCRYPTED_PACKAGE_IMPORT_NOT_READY'):
        packages.import_bytes(b'bad',OWNER)


def test_v10513_local_runtime_and_normal_hosted_not_connected():
    runtime=(ROOT/'src/runtime/hosted.py').read_text()
    assert 'encrypted_fields=' not in runtime
    local=(ROOT/'src/runtime/local.py').read_text()
    assert 'HostedPrivateFirestoreFields' not in local and 'cloud_kms' not in local
    assert 'google-cloud-kms' not in (ROOT/'requirements.txt').read_text()
    coverage=json.loads((ROOT/'docs/HOSTED_PRIVACY_COVERAGE_M12B.json').read_text())
    assert coverage['auto_activate'] is False
    assert coverage['firestore_models']['task']['encryption_coverage']=='NOT_INTEGRATED'
