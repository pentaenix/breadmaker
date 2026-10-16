"""M12L private restore and independent inventory regression contracts.

The human runs ./verify-release; this test module must never be run by the agent.
"""
from __future__ import annotations

import secrets
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))

from hosted.encrypted_recovery import HostedEncryptedRecoveryReader, HostedEncryptedRecoveryWriter
from hosted.encrypted_live_restore import HostedEncryptedLiveRestorer, HostedEncryptedLiveRestoreError
from hosted.firestore.memory import MemoryDocumentStore
from hosted.firestore.repositories import HostedProjectRepository, HostedUserRepository, HostedStructuredRepository
from hosted.recovery_coverage import RecoveryCoverageError, physical_project_fingerprint
from hosted.runtime_services import HostedCloudProjectRepository, ensure_hosted_user
from hosted.security.envelope import HostedEnvelopeCodec
from hosted.security.private_firestore import HostedPrivateFirestoreFields
from hosted.storage.encrypted_objects import HostedEncryptedObjectStore
from hosted.storage.memory import MemoryObjectStore

OWNER = 'usr_' + 'a' * 32
DEST = 'usr_' + 'b' * 32
RESOURCE = 'projects/breadmaker-test-123/locations/us-central1/keyRings/hosted/cryptoKeys/data'


class PrivateKms:
    key_resource = RESOURCE

    def __init__(self):
        self.key = AESGCM.generate_key(bit_length=256)

    def wrap(self, plaintext):
        nonce = secrets.token_bytes(12)
        return nonce + AESGCM(self.key).encrypt(nonce, plaintext, b'private-restore-kms')

    def unwrap(self, wrapped):
        return AESGCM(self.key).decrypt(wrapped[:12], wrapped[12:], b'private-restore-kms')


def private_store(owner, *, docs=None, live=None, kms=None):
    docs, live = docs or MemoryDocumentStore(), live or MemoryObjectStore()
    codec = HostedEnvelopeCodec(kms or PrivateKms())
    ensure_hosted_user(docs, user_id=owner, display_name='Writer')
    authority = HostedProjectRepository(docs)
    users = HostedUserRepository(HostedStructuredRepository(docs))
    private_objects = HostedEncryptedObjectStore(
        live, codec, authorize=lambda pid, uid: authority.get_for_user(pid, uid))
    projects = HostedCloudProjectRepository(
        docs, live, authority, users, lambda: owner,
        encrypted_fields=HostedPrivateFirestoreFields(codec),
        encrypted_objects=private_objects)
    return docs, live, projects, codec


def source_archive():
    docs, live, projects, codec = private_store(OWNER)
    pid = projects.create('Unpublished project')['id']
    projects.domain.create('task', {'project_id': pid}, {'title': 'Private note'},
                           user_id=OWNER, entity_id='task_keep')
    projects.domain.update('manuscript_block', {'project_id': pid, 'block_id': 'b_1'},
                           {'text_content': 'Unpublished secret',
                            'content_html': '<p>Unpublished secret</p>'},
                           user_id=OWNER, operation='source_private_edit')
    bucket = MemoryObjectStore()
    HostedEncryptedRecoveryWriter(projects, bucket, codec).create(pid, OWNER, operation='private_backup')
    reader = HostedEncryptedRecoveryReader(
        bucket, codec, authorize=lambda p, u, sid: p == pid and u == OWNER)
    return pid, reader


def test_v10524_private_restore_reencrypts_under_new_project_and_new_kms():
    source_pid, reader = source_archive()
    docs, live, destination, dest_codec = private_store(DEST)
    restorer = HostedEncryptedLiveRestorer(reader, destination,
                                          authorize_destination=lambda uid: uid == DEST)
    restored = restorer.restore(source_pid, OWNER, DEST, operation='private_backup',
                                restore_operation='restore_new_kms_01')
    pid = restored.destination_project_id
    assert pid != source_pid
    assert restored.restored_entity_count >= 4
    assert destination.authority.get_for_user(pid, DEST)['title'] == 'Unpublished project'
    task = destination.domain.get('task', {'project_id': pid, 'task_id': 'task_keep'}, user_id=DEST)
    assert task['title'] == 'Private note'
    block = destination.domain.get('manuscript_block', {'project_id': pid, 'block_id': 'b_1'}, user_id=DEST)
    assert block['text_content'] == 'Unpublished secret'
    raw_task = docs.get(('projects', pid, 'tasks', 'task_keep'))
    assert 'Private note' not in str(raw_task.data)
    with pytest.raises(Exception):
        dest_codec.open(raw_task.data['_bm_private_fields'],
                        context={'project_id': source_pid, 'entity': 'task', 'record_id': 'task_keep'})
    objects = live.list_prefix(f'projects/{pid}/')
    assert objects
    for meta in objects:
        assert '/private/v1/manuscript_block/' in meta.name
        ciphertext = b''.join(live.read_chunks(meta.name, max_bytes=24 * 1024 * 1024))
        assert b'Unpublished secret' not in ciphertext
    receipt = docs.get(('hosted_private_restore_operations', 'restore_new_kms_01'))
    assert receipt.data['status'] == 'committed'
    with pytest.raises(HostedEncryptedLiveRestoreError, match='OPERATION_REUSED'):
        restorer.restore(source_pid, OWNER, DEST, operation='private_backup',
                         restore_operation='restore_new_kms_01')


def test_v10524_destination_denial_before_any_project_creation():
    source_pid, reader = source_archive()
    docs, _live, destination, _codec = private_store(DEST)
    restorer = HostedEncryptedLiveRestorer(reader, destination,
                                          authorize_destination=lambda uid: False)
    with pytest.raises(PermissionError, match='DESTINATION_DENIED'):
        restorer.restore(source_pid, OWNER, DEST, operation='private_backup',
                         restore_operation='denied_restore_01')
    assert docs.get(('hosted_private_restore_operations', 'denied_restore_01')) is None
    assert not destination.list(DEST)


def test_v10524_failed_stage_is_not_visible_to_destination(monkeypatch):
    source_pid, reader = source_archive()
    docs, _live, destination, _codec = private_store(DEST)
    original_seal = destination.domain.encrypted_fields.seal
    count = [0]

    def fail_second(*args, **kwargs):
        count[0] += 1
        if count[0] > 1:
            raise RuntimeError('injected staging failure')
        return original_seal(*args, **kwargs)

    monkeypatch.setattr(destination.domain.encrypted_fields, 'seal', fail_second)
    restorer = HostedEncryptedLiveRestorer(reader, destination,
                                          authorize_destination=lambda uid: uid == DEST)
    with pytest.raises(HostedEncryptedLiveRestoreError, match='STAGING_FAILED'):
        restorer.restore(source_pid, OWNER, DEST, operation='private_backup',
                         restore_operation='failed_staging_01')
    receipt = docs.get(('hosted_private_restore_operations', 'failed_staging_01'))
    assert receipt is not None and receipt.data['status'] == 'staging'
    assert docs.get(('projects', receipt.data['destination_project_id'])) is None
    assert not destination.list(DEST)


def test_v10524_unregistered_firestore_nested_records_fail_closed_before_publication():
    docs, _live, projects, codec = private_store(OWNER)
    pid = projects.create('Project')['id']
    docs.run_transaction(lambda tx: tx.create(
        ('projects', pid, 'reviews', 'review_orphan', 'unlisted_private', 'unknown_1'),
        {'content': 'Invisible'}))
    assert any(path[-1] == 'unknown_1' for path, _ in physical_project_fingerprint(docs, pid))
    bucket = MemoryObjectStore()
    with pytest.raises(RecoveryCoverageError, match='UNKNOWN_OR_ORPHANED_RECORD'):
        HostedEncryptedRecoveryWriter(projects, bucket, codec).create(pid, OWNER, operation='orphan_block')
    assert not bucket.list_prefix(f'projects/{pid}/private/v1/')


def test_v10524_unknown_live_gcs_object_blocks_private_backup():
    docs, live, projects, codec = private_store(OWNER)
    pid = projects.create('Project')['id']
    live.write_chunks(f'projects/{pid}/unregistered/secret.bin', (b'plaintext',),
                      max_bytes=100, content_type='application/octet-stream',
                      if_generation_match=0)
    bucket = MemoryObjectStore()
    with pytest.raises(RecoveryCoverageError, match='UNSUPPORTED_GCS_OBJECT'):
        HostedEncryptedRecoveryWriter(projects, bucket, codec).create(pid, OWNER, operation='gcs_gap')
    assert not bucket.list_prefix(f'projects/{pid}/private/v1/')


def test_v10524_unconverted_asset_and_configuration_cannot_write_plaintext_in_private_mode():
    docs, live, projects, _ = private_store(OWNER)
    pid = projects.create('Project')['id']
    with pytest.raises(Exception, match='HOSTED_PRIVATE_ASSET_POINTER_REQUIRED'):
        projects.domain.create('project_asset', {'project_id': pid}, {'name': 'Never plaintext'},
                               user_id=OWNER, entity_id='asset_forbidden')
    with pytest.raises(Exception, match='HOSTED_PRIVATE_UNSUPPORTED_ENTITY'):
        projects.domain.create('backup_destination_configuration', {'project_id': pid},
                               {'secret': 'Never plaintext'}, user_id=OWNER, entity_id='destination_bad')
    assert not live.list_prefix(f'projects/{pid}/cuts/')


def test_v10524_restore_postcommit_receipt_failure_is_reconcilable(monkeypatch):
    """Losing the final receipt ACK never creates a duplicate visible project.

    The only recovery permit is the already-published *canonical* project+owner
    authority, whose creation followed all staged destination readback checks.
    """
    source_pid, reader = source_archive()
    docs, _live, destination, _codec = private_store(DEST)
    restorer = HostedEncryptedLiveRestorer(reader, destination,
                                          authorize_destination=lambda uid: uid == DEST)
    original_create = destination.authority.create
    original_run = docs.run_transaction
    fault = [False]

    def publish_then_lose_receipt(*args, **kwargs):
        published = original_create(*args, **kwargs)
        assert not fault[0]
        fault[0] = True

        def fail_once(callback):
            docs.run_transaction = original_run
            raise RuntimeError('simulated process failure after authority commit')

        docs.run_transaction = fail_once
        return published

    monkeypatch.setattr(destination.authority, 'create', publish_then_lose_receipt)
    with pytest.raises(RuntimeError, match='simulated process failure'):
        restorer.restore(source_pid, OWNER, DEST, operation='private_backup',
                         restore_operation='postcommit_receipt_loss')
    monkeypatch.setattr(destination.authority, 'create', original_create)
    assert fault[0]
    receipt_path = ('hosted_private_restore_operations', 'postcommit_receipt_loss')
    receipt = docs.get(receipt_path)
    assert receipt.data['status'] == 'staging'
    original_pid = receipt.data['destination_project_id']
    assert destination.authority.get_for_user(original_pid, DEST)['title'] == 'Unpublished project'
    before = {item['id'] for item in destination.list(DEST)}
    recovered = restorer.restore(source_pid, OWNER, DEST, operation='private_backup',
                                 restore_operation='postcommit_receipt_loss')
    assert recovered.destination_project_id == original_pid
    assert docs.get(receipt_path).data['status'] == 'committed'
    assert {item['id'] for item in destination.list(DEST)} == before
    assert destination.domain.get('task', {'project_id': original_pid, 'task_id': 'task_keep'},
                                  user_id=DEST)['title'] == 'Private note'
    # Ordinary completed operations are not replayed without a fresh request.
    with pytest.raises(HostedEncryptedLiveRestoreError, match='OPERATION_REUSED'):
        restorer.restore(source_pid, OWNER, DEST, operation='private_backup',
                         restore_operation='postcommit_receipt_loss')
