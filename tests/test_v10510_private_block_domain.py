"""M12D opt-in hosted manuscript-block persistence; HUMAN RUNS TESTS ONLY.

No Google credentials needed. The injected key wrapper is an offline test fake;
this is not deployment approval or proof of encrypted recovery archives.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))

from hosted.chapter_mutations import replace_chapter_blocks
from hosted.firestore import (
    HostedProjectRepository, HostedStructuredRepository, HostedUserRepository,
    MemoryDocumentStore, ProjectUnavailable,
)
from hosted.firestore.contracts import HostedValidationError
from hosted.runtime_services import HostedCloudProjectRepository, ensure_hosted_user
from hosted.security.envelope import HostedEnvelopeCodec
from hosted.storage.encrypted_objects import HostedEncryptedObjectStore
from hosted.storage.contracts import ObjectIntegrityError
from hosted.storage.memory import MemoryObjectStore
from hosted.packages import HostedProjectPackageService

OWNER = 'usr_' + 'a' * 32
OUTSIDER = 'usr_' + 'b' * 32
RESOURCE = 'projects/breadmaker-test-123/locations/us-central1/keyRings/hosted/cryptoKeys/data'
SECRET = 'Unpublished draft: the purple chamber is sealed.'


class FakeKms:
    key_resource = RESOURCE

    def __init__(self):
        self._key = AESGCM.generate_key(bit_length=256)

    def wrap(self, key):
        nonce = secrets.token_bytes(12)
        return nonce + AESGCM(self._key).encrypt(nonce, key, b'wrap')

    def unwrap(self, payload):
        return AESGCM(self._key).decrypt(payload[:12], payload[12:], b'wrap')


def setup_private():
    documents, objects = MemoryDocumentStore(), MemoryObjectStore()
    ensure_hosted_user(documents, user_id=OWNER, display_name='Writer')
    ensure_hosted_user(documents, user_id=OUTSIDER, display_name='Stranger')
    authority = HostedProjectRepository(documents)
    users = HostedUserRepository(HostedStructuredRepository(documents))
    kms = FakeKms()
    authorizer = lambda pid, uid: authority.get_for_user(pid, uid)
    encrypted = HostedEncryptedObjectStore(objects, HostedEnvelopeCodec(kms), authorize=authorizer)
    projects = HostedCloudProjectRepository(documents, objects, authority, users, lambda: OWNER,
                                             encrypted_objects=encrypted)
    pid = projects.create('Private draft')['id']
    return documents, objects, authority, users, encrypted, projects, pid


def raw_block(documents, pid, block_id='b_1'):
    value = documents.get(('projects', pid, 'blocks', block_id))
    assert value is not None
    return dict(value.data)


def test_v10510_real_hosted_project_seeds_ciphertext_and_recovers_from_new_replica():
    documents, objects, authority, users, encrypted, projects, pid = setup_private()
    block = projects.domain.get('manuscript_block', {'project_id':pid,'block_id':'b_1'}, user_id=OWNER)
    assert block['text_content'] == ''
    changed = projects.domain.update('manuscript_block', {'project_id':pid,'block_id':'b_1'},
                                     {'text_content':SECRET,'content_html':'<p>'+SECRET+'</p>'},
                                     user_id=OWNER, operation='update_private_draft')
    assert changed['text_content'] == SECRET
    stored = raw_block(documents,pid)
    assert 'text_content' not in stored and 'content_html' not in stored
    assert SECRET not in json.dumps(stored)
    pointer = stored['payload_object']
    assert pointer['encryption_format'] == 'bm-hosted-envelope-v1'
    assert 'sha256/' not in pointer['object_name']
    assert hashlib.sha256(SECRET.encode()).hexdigest() not in pointer['object_name']
    raw = b''.join(objects.read_chunks(pointer['object_name'],max_bytes=24*1024*1024))
    assert SECRET.encode() not in raw
    assert SECRET not in raw.decode('utf-8')
    again = HostedCloudProjectRepository(documents, objects, authority, users, lambda: OWNER,
                                          encrypted_objects=encrypted)
    assert again.domain.get('manuscript_block',{'project_id':pid,'block_id':'b_1'},user_id=OWNER)['text_content'] == SECRET
    assert again.domain.list('manuscript_block',{'project_id':pid},user_id=OWNER)[0]['text_content'] == SECRET


def test_v10510_private_create_replays_do_not_leak_content_hashes():
    docs, objects, authority, users, encrypted, projects, pid = setup_private()
    payload = {'chapter_id':'chapter_1','tab_id':'tab_manuscript','position':1,
               'text_content':SECRET,'content_html':'<p>'+SECRET+'</p>'}
    before = projects.domain.project_generation(pid,OWNER)
    first = projects.domain.create('manuscript_block',{'project_id':pid},payload,user_id=OWNER,
                                   entity_id='b_private',operation='same_create')
    count = len(objects.list_prefix('projects/'+pid+'/private/'))
    repeat = projects.domain.create('manuscript_block',{'project_id':pid},payload,user_id=OWNER,
                                    entity_id='b_private',operation='same_create')
    assert first['text_content'] == SECRET
    assert repeat['text_content'] == SECRET
    assert len(objects.list_prefix('projects/'+pid+'/private/')) == count
    assert projects.domain.project_generation(pid,OWNER) == before + 1
    stored = raw_block(docs,pid,'b_private')
    assert hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest() != stored['_bm_create_fingerprint']
    with pytest.raises(ObjectIntegrityError,match='HOSTED_ENCRYPTED_OPERATION_REUSED'):
        projects.domain.create('manuscript_block',{'project_id':pid},
                               {**payload,'text_content':'changed'},user_id=OWNER,
                               entity_id='b_private',operation='same_create')


def test_v10510_atomic_chapter_replacement_publishes_only_ciphertext():
    docs, objects, _authority, _users, _encrypted, projects, pid = setup_private()
    original_generation = projects.domain.project_generation(pid,OWNER)
    result = replace_chapter_blocks(
        projects.domain, project_id=pid, chapter_id='chapter_1', user_id=OWNER,
        blocks=[{'id':'b_1','text_content':SECRET,'content_html':'<p>'+SECRET+'</p>'},
                {'text_content':'Second encrypted paragraph', 'content_html':'<p>Second encrypted paragraph</p>'}],
        expected_revision=original_generation, operation='replace_private_chapter',
    )
    assert len(result['blocks']) == 2
    assert result['blocks'][0]['text_content'] == SECRET
    rows = docs.query_collection(('projects',pid,'blocks'))
    assert all('text_content' not in row.data for row in rows)
    assert all(row.data['payload_object']['encryption_format'] == 'bm-hosted-envelope-v1' for row in rows)
    for meta in objects.list_prefix('projects/'+pid+'/private/'):
        assert SECRET.encode() not in b''.join(objects.read_chunks(meta.name,max_bytes=24*1024*1024))
    assert SECRET not in str(docs.get(('projects',pid,'mutation_receipts',hashlib.sha256('replace_private_chapter'.encode()).hexdigest()[:40])).data)


def test_v10510_unauthorized_reader_denied_before_encrypted_object_read():
    _docs, _objects, _authority, _users, _encrypted, projects, pid = setup_private()
    with pytest.raises(ProjectUnavailable):
        projects.domain.get('manuscript_block',{'project_id':pid,'block_id':'b_1'},user_id=OUTSIDER)
    with pytest.raises(ProjectUnavailable):
        projects.domain.update('manuscript_block',{'project_id':pid,'block_id':'b_1'},
                               {'text_content':SECRET},user_id=OUTSIDER)


def test_v10510_encrypted_mode_refuses_legacy_plaintext_and_unavailable_key():
    docs, objects, authority, users, encrypted, projects, pid = setup_private()
    def modify(tx):
        path=('projects',pid,'blocks','b_1')
        data=dict(tx.get(path).data)
        data['payload_object'].pop('encryption_format')
        tx.set(path,data)
    docs.run_transaction(modify)
    with pytest.raises(HostedValidationError,match='HOSTED_ENCRYPTED_BLOCK_REQUIRED'):
        projects.domain.get('manuscript_block',{'project_id':pid,'block_id':'b_1'},user_id=OWNER)
    # An old plaintext document is NOT transparently adopted in the encrypted mode.
    legacy = HostedCloudProjectRepository(docs,objects,authority,users,lambda: OWNER)
    assert legacy.domain.encrypted_objects is None


def test_v10510_package_export_import_fail_closed_for_experimental_encryption():
    docs, objects, _authority, _users, _encrypted, projects, pid = setup_private()
    packages=HostedProjectPackageService(projects)
    with pytest.raises(ValueError,match='HOSTED_ENCRYPTED_PACKAGE_EXPORT_NOT_READY'):
        packages.export_bytes(pid,OWNER)
    with pytest.raises(ValueError,match='HOSTED_ENCRYPTED_PACKAGE_IMPORT_NOT_READY'):
        packages.import_bytes(b'invalid',OWNER)
    with pytest.raises(ValueError,match='HOSTED_ENCRYPTED_PACKAGE_IMPORT_NOT_READY'):
        packages.import_json({},OWNER)


def test_v10510_encrypted_mode_not_runtime_default_or_local_dependency():
    runtime=(ROOT/'src/runtime/hosted.py').read_text()
    assert 'encrypted_objects=' not in runtime
    local=(ROOT/'src/runtime/local.py').read_text()
    assert 'HostedEncryptedObjectStore' not in local and 'CloudKms' not in local
    assert 'google-cloud-kms' not in (ROOT/'requirements.txt').read_text()
    coverage=json.loads((ROOT/'docs/HOSTED_PRIVACY_COVERAGE_M12B.json').read_text())
    assert coverage['auto_activate'] is False
    assert coverage['firestore_models']['manuscript_block']['encryption_coverage']=='NOT_INTEGRATED'
    assert coverage['encrypted_object_pilot']['status']=='OPT_IN_NOT_WIRED'


def test_v10510_unknown_private_firestore_metadata_fails_before_object_publication():
    docs, objects, _authority, _users, _encrypted, projects, pid = setup_private()
    count=len(objects.list_prefix('projects/'+pid+'/private/'))
    with pytest.raises(HostedValidationError,match='HOSTED_ENCRYPTED_BLOCK_METADATA_FORBIDDEN'):
        projects.domain.create('manuscript_block',{'project_id':pid},
            {'chapter_id':'chapter_1','tab_id':'tab_manuscript','position':2,
             'text_content':SECRET,'content_html':'<p>'+SECRET+'</p>',
             'unclassified_private_notes':SECRET},
            user_id=OWNER,entity_id='b_bad_private',operation='reject_private_note')
    assert len(objects.list_prefix('projects/'+pid+'/private/'))==count


def test_v10510_swapped_block_pointer_and_wrong_kms_cannot_read_plaintext():
    docs, objects, authority, users, encrypted, projects, pid = setup_private()
    projects.domain.update('manuscript_block',{'project_id':pid,'block_id':'b_1'},
                           {'text_content':SECRET},user_id=OWNER,operation='private_pointer_one')
    source = raw_block(docs,pid)
    projects.domain.create('manuscript_block', {'project_id':pid},
                           {'chapter_id':'chapter_1','tab_id':'tab_manuscript','position':2,
                            'text_content':'second'},
                           user_id=OWNER,entity_id='b_other',operation='private_pointer_two')
    def mutate(tx):
        path = ('projects',pid,'blocks','b_other')
        copy = dict(tx.get(path).data)
        copy['payload_object'] = dict(source['payload_object'])
        tx.set(path,copy)
    docs.run_transaction(mutate)
    with pytest.raises(ObjectIntegrityError,match='HOSTED_ENCRYPTED_REFERENCE_SCOPE_MISMATCH'):
        projects.domain.get('manuscript_block',{'project_id':pid,'block_id':'b_other'},user_id=OWNER)
    wrong = HostedEncryptedObjectStore(objects, HostedEnvelopeCodec(FakeKms()),
                    authorize=lambda project_id,user_id: authority.get_for_user(project_id,user_id))
    reopened=HostedCloudProjectRepository(docs,objects,authority,users,lambda: OWNER,
                                           encrypted_objects=wrong)
    with pytest.raises(ObjectIntegrityError,match='HOSTED_ENCRYPTED_AUTHENTICATION_FAILED'):
        reopened.domain.get('manuscript_block',{'project_id':pid,'block_id':'b_1'},user_id=OWNER)
