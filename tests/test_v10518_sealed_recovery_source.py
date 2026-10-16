"""M12I: owner-only encrypted recovery from opt-in private Firestore + GCS sources.

Source conformance only. The human owner alone runs the verifier; agents must
never run this file, pytest or Playwright.
"""
from __future__ import annotations

import hashlib
import io
import json
import secrets
import sys
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))

from hosted.encrypted_recovery import HostedEncryptedRecoveryReader, HostedEncryptedRecoveryWriter
from hosted.firestore import HostedProjectRepository, HostedStructuredRepository, HostedUserRepository, MemoryDocumentStore
from hosted.firestore.contracts import HostedAuthorizationError, ProjectUnavailable
from hosted.packages import HostedProjectPackageService
from hosted.runtime_services import HostedCloudProjectRepository, ensure_hosted_user
from hosted.security.envelope import HostedEnvelopeCodec
from hosted.security.private_firestore import HostedPrivateFirestoreFields
from hosted.storage.encrypted_objects import HostedEncryptedObjectStore
from hosted.storage.memory import MemoryObjectStore

OWNER = 'usr_' + 'a' * 32
STRANGER = 'usr_' + 'b' * 32
RESOURCE = 'projects/breadmaker-test-123/locations/us-central1/keyRings/hosted/cryptoKeys/data'
SECRET = 'Sealed chapter: the secret wisteria passage'


class OfflineKms:
    key_resource = RESOURCE

    def __init__(self):
        self.key = AESGCM.generate_key(bit_length=256)
        self.available = True

    def wrap(self, data):
        if not self.available:
            raise RuntimeError('do not leak key backend details')
        nonce = secrets.token_bytes(12)
        return nonce + AESGCM(self.key).encrypt(nonce, data, b'm12i-keywrap')

    def unwrap(self, data):
        if not self.available:
            raise RuntimeError('do not leak key backend details')
        return AESGCM(self.key).decrypt(data[:12], data[12:], b'm12i-keywrap')


def setup_private(*, fields=True, objects=True):
    docs, live, recovery = MemoryDocumentStore(), MemoryObjectStore(), MemoryObjectStore()
    ensure_hosted_user(docs, user_id=OWNER, display_name='Author')
    ensure_hosted_user(docs, user_id=STRANGER, display_name='Intruder')
    authority = HostedProjectRepository(docs)
    users = HostedUserRepository(HostedStructuredRepository(docs))
    kms = OfflineKms()
    codec = HostedEnvelopeCodec(kms)
    private_fields = HostedPrivateFirestoreFields(codec) if fields else None
    encrypted_objects = HostedEncryptedObjectStore(live, codec,
        authorize=lambda pid, uid: authority.get_for_user(pid, uid)) if objects else None
    projects = HostedCloudProjectRepository(docs, live, authority, users, lambda: OWNER,
        encrypted_fields=private_fields, encrypted_objects=encrypted_objects)
    pid = projects.create('The unpublished chapter')['id']
    return docs, live, recovery, kms, codec, projects, pid


def test_v10518_encrypted_firestore_and_block_source_can_be_independently_restored():
    docs, live, recovery, kms, codec, projects, pid = setup_private()
    projects.domain.create('task', {'project_id': pid}, {'title': SECRET, 'description': SECRET},
                           user_id=OWNER, entity_id='task_secret')
    projects.domain.update('manuscript_block', {'project_id': pid, 'block_id': 'b_1'},
                           {'text_content': SECRET, 'content_html': '<p>' + SECRET + '</p>'},
                           user_id=OWNER, operation='m12i_change_block')
    raw_task = docs.get(('projects', pid, 'tasks', 'task_secret')).data
    raw_block = docs.get(('projects', pid, 'blocks', 'b_1')).data
    assert SECRET not in json.dumps(raw_task) + json.dumps(raw_block)
    assert SECRET not in ''.join(
        b''.join(live.read_chunks(row.name, max_bytes=24 * 1024 * 1024)).decode('utf-8')
        for row in live.list_prefix(f'projects/{pid}/private/'))
    package = HostedProjectPackageService(projects)
    with pytest.raises(ValueError, match='HOSTED_ENCRYPTED_PACKAGE_EXPORT_NOT_READY'):
        package.export_bytes(pid, OWNER)
    with pytest.raises(ValueError, match='HOSTED_ENCRYPTED_PACKAGE_EXPORT_NOT_READY'):
        package.export_json(pid, OWNER)
    with pytest.raises(ValueError, match='HOSTED_ENCRYPTED_PACKAGE_IMPORT_NOT_READY'):
        package.import_bytes(b'archive', OWNER)
    writer = HostedEncryptedRecoveryWriter(projects, recovery, codec)
    result = writer.create(pid, OWNER, operation='private_source_snapshot')
    records = recovery.list_prefix(f'projects/{pid}/private/v1/')
    assert len(records) == result.chunk_count + 1
    for meta in records:
        raw = b''.join(recovery.read_chunks(meta.name, max_bytes=24 * 1024 * 1024))
        assert SECRET.encode() not in raw
        assert hashlib.sha256(SECRET.encode()).hexdigest() not in meta.name
    reader = HostedEncryptedRecoveryReader(recovery, codec,
        authorize=lambda p, u, sid: p == pid and u == OWNER)
    # The original Firestore and live GCS are deliberately inaccessible below.
    del projects, docs, live, writer, package, raw_task, raw_block
    payload = reader.read_offline(pid, OWNER, operation='private_source_snapshot')
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entities = json.loads(archive.read('project.json'))['entities']
        assert any(SECRET == row['data'].get('title') for row in entities['task'])
        assert any(SECRET == row['data'].get('text_content') for row in entities['manuscript_block'])
    result = reader.drill_offline(pid, OWNER, operation='private_source_snapshot')
    assert result.isolated and result.semantic_match


def test_v10518_full_mode_is_mandatory_for_sealed_private_source():
    for private_fields, private_objects in ((True, False), (False, True)):
        _docs, _live, recovery, _kms, codec, projects, pid = setup_private(
            fields=private_fields, objects=private_objects)
        with pytest.raises(ValueError, match='HOSTED_SEALED_RECOVERY_REQUIRES_BOTH_CODECS'):
            HostedEncryptedRecoveryWriter(projects, recovery, codec).create(
                pid, OWNER, operation='mixed_mode')
        assert not recovery.list_prefix(f'projects/{pid}/private/v1/')


def test_v10518_non_owner_cannot_export_decrypted_private_source():
    _docs, _live, recovery, _kms, codec, projects, pid = setup_private()
    with pytest.raises((ProjectUnavailable, HostedAuthorizationError)):
        HostedEncryptedRecoveryWriter(projects, recovery, codec).create(pid, STRANGER, operation='forbidden')
    with pytest.raises((ProjectUnavailable, HostedAuthorizationError)):
        HostedProjectPackageService(projects)._sealed_recovery_source(pid, STRANGER)
    assert not recovery.list_prefix(f'projects/{pid}/private/v1/')


def test_v10518_unavailable_key_never_creates_partial_recovery_snapshot():
    _docs, _live, recovery, kms, codec, projects, pid = setup_private()
    projects.domain.create('task', {'project_id': pid}, {'title': SECRET}, user_id=OWNER)
    kms.available = False
    with pytest.raises(Exception) as error:
        HostedEncryptedRecoveryWriter(projects, recovery, codec).create(pid, OWNER, operation='no_key')
    assert 'do not leak key backend details' not in str(error.value)
    assert not recovery.list_prefix(f'projects/{pid}/private/v1/')


def test_v10518_old_public_plaintext_archive_writer_still_fails_closed():
    from hosted.recovery_archives import HostedRecoveryArchiveService
    _docs, _live, recovery, _kms, codec, projects, pid = setup_private()
    with pytest.raises(ValueError, match='HOSTED_ENCRYPTED_PACKAGE_EXPORT_NOT_READY'):
        HostedRecoveryArchiveService(projects, recovery).create(pid, OWNER, operation='plaintext_denied')
    assert not recovery.list_prefix('recovery/v1/')


def test_v10518_plaintext_local_and_normal_hosted_export_do_not_change():
    text = (ROOT / 'src/runtime/local.py').read_text(encoding='utf-8')
    assert 'hosted.encrypted_recovery' not in text and 'HostedPrivateFirestoreFields' not in text
    schedule = (ROOT / 'src/hosted/recovery_schedule.py').read_text(encoding='utf-8')
    assert 'HostedEncryptedRecoveryWriter' not in schedule
    requirements = (ROOT / 'requirements.txt').read_text(encoding='utf-8')
    assert 'google-cloud-kms' not in requirements
    _docs, _live, recovery, _kms, codec, projects, pid = setup_private(fields=False, objects=False)
    assert HostedProjectPackageService(projects).export_bytes(pid, OWNER).startswith(b'PK')
    result = HostedEncryptedRecoveryWriter(projects, recovery, codec).create(pid, OWNER, operation='legacy_src')
    assert result.chunk_count == 1
