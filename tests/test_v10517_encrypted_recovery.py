"""M12H encrypted recovery source contracts. ONLY the owner runs verifier."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
import secrets
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))

from hosted.encrypted_recovery import HostedEncryptedRecoveryReader, HostedEncryptedRecoveryWriter
from hosted.firestore.contracts import DocumentConflict, HostedValidationError
from hosted.firestore.memory import MemoryDocumentStore
from hosted.packages import HostedProjectPackageService
from hosted.runtime_services import _DEV_USER_ID
from hosted.security.envelope import HostedEnvelopeCodec
from hosted.storage.contracts import ObjectAlreadyExists, ObjectIntegrityError, ObjectNotFound
from hosted.storage.memory import MemoryObjectStore
from runtime.hosted import build_hosted_dev_runtime

RESOURCE = 'projects/breadmaker-test-123/locations/us-central1/keyRings/hosted/cryptoKeys/data'
OTHER = 'usr_' + 'e' * 32


class OfflineKms:
    key_resource = RESOURCE

    def __init__(self):
        self.key = AESGCM.generate_key(bit_length=256)
        self.enabled = True

    def wrap(self, data):
        if not self.enabled:
            raise RuntimeError('sensitive KMS error')
        nonce = secrets.token_bytes(12)
        return nonce + AESGCM(self.key).encrypt(nonce, data, b'recovery-wrap')

    def unwrap(self, data):
        if not self.enabled:
            raise RuntimeError('sensitive KMS error')
        return AESGCM(self.key).decrypt(data[:12], data[12:], b'recovery-wrap')


def setup(monkeypatch, *, recovery=None, kms=None):
    monkeypatch.setenv('BREADMAKER_AUTH_MODE', 'developer')
    monkeypatch.setenv('BREADMAKER_ENV', 'development')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED', '1')
    docs, live = MemoryDocumentStore(), MemoryObjectStore()
    recovery, kms = recovery or MemoryObjectStore(), kms or OfflineKms()
    runtime = build_hosted_dev_runtime(documents=docs, objects=live)
    pid = runtime.projects.create('Unpublished novel')['id']
    writer = HostedEncryptedRecoveryWriter(runtime.projects, recovery, HostedEnvelopeCodec(kms))
    reader = HostedEncryptedRecoveryReader(
        recovery, HostedEnvelopeCodec(kms), authorize=lambda p, u, s: p == pid and u == _DEV_USER_ID)
    return runtime, docs, live, recovery, kms, pid, writer, reader


def test_v10517_archive_ciphertext_only_and_independent_restore_after_source_loss(monkeypatch):
    runtime, docs, live, bucket, _, pid, writer, reader = setup(monkeypatch)
    runtime.projects.rename(pid, 'Extremely private plot twist')
    result = writer.create(pid, _DEV_USER_ID, operation='first_backup')
    assert result.manifest_name.endswith('/commit.bme')
    assert result.chunk_count >= 1
    assert result.archive_bytes > 0
    rows = bucket.list_prefix(f'projects/{pid}/private/v1/')
    assert len(rows) == result.chunk_count + 1
    for meta in rows:
        stored = b''.join(bucket.read_chunks(meta.name, max_bytes=24 * 1024 * 1024))
        assert b'Extremely private plot twist' not in stored
        assert b'"manifest.json"' not in stored
        assert hashlib.sha256(b'Extremely private plot twist').hexdigest() not in meta.name
    # No original project, source object store, or runtime passed to reader.
    del runtime, docs, live, writer
    recovered = reader.read_offline(pid, _DEV_USER_ID, operation='first_backup')
    with zipfile.ZipFile(io.BytesIO(recovered)) as package:
        contents = package.read('manifest.json')
    assert b'Extremely private plot twist' in contents
    drill = reader.drill_offline(pid, _DEV_USER_ID, operation='first_backup')
    assert drill.semantic_match and drill.isolated


def test_v10517_deterministic_retry_reuses_immutable_manifest_and_chunks(monkeypatch):
    runtime, _, _, bucket, _, pid, writer, reader = setup(monkeypatch)
    first = writer.create(pid, _DEV_USER_ID, operation='stable')
    again = writer.create(pid, _DEV_USER_ID, operation='stable')
    assert first == again
    assert len(bucket.list_prefix(f'projects/{pid}/private/v1/')) == first.chunk_count + 1
    runtime.projects.rename(pid, 'A later revision')
    assert writer.create(pid, _DEV_USER_ID, operation='stable') == first
    later = writer.create(pid, _DEV_USER_ID, operation='later')
    assert later.manifest_name != first.manifest_name
    with zipfile.ZipFile(io.BytesIO(reader.read_offline(pid, _DEV_USER_ID, operation='stable'))) as package:
        assert b'A later revision' not in package.read('manifest.json')


def test_v10517_non_owner_rejected_before_object_access(monkeypatch):
    _, _, _, bucket, _, pid, writer, _ = setup(monkeypatch)
    with pytest.raises(Exception):
        writer.create(pid, OTHER, operation='bad')
    assert not bucket.list_prefix(f'projects/{pid}/private/v1/')


def test_v10517_external_operator_admission_required_before_object_access(monkeypatch):
    _, _, _, bucket, kms, pid, writer, _ = setup(monkeypatch)
    writer.create(pid, _DEV_USER_ID, operation='protected')

    class ForbiddenRead(MemoryObjectStore):
        def stat(self, name):
            raise AssertionError('bucket read before operator authorization')

    reject = HostedEncryptedRecoveryReader(
        ForbiddenRead(), HostedEnvelopeCodec(kms), authorize=lambda *_: False)
    with pytest.raises(PermissionError, match='HOSTED_ENCRYPTED_RECOVERY_NOT_AUTHORIZED'):
        reject.read_offline(pid, _DEV_USER_ID, operation='protected')
    with pytest.raises(ValueError, match='HOSTED_ENCRYPTED_RECOVERY_AUTH_REQUIRED'):
        HostedEncryptedRecoveryReader(bucket, HostedEnvelopeCodec(kms), authorize=None)


def test_v10517_wrong_owner_operation_or_kms_key_fail_closed(monkeypatch):
    _, _, _, bucket, _, pid, writer, reader = setup(monkeypatch)
    writer.create(pid, _DEV_USER_ID, operation='once')
    with pytest.raises(PermissionError):
        reader.read_offline(pid, OTHER, operation='once')
    with pytest.raises(ObjectNotFound):
        reader.read_offline(pid, _DEV_USER_ID, operation='other')
    wrong_key = HostedEncryptedRecoveryReader(
        bucket, HostedEnvelopeCodec(OfflineKms()), authorize=lambda *_: True)
    with pytest.raises(ObjectIntegrityError):
        wrong_key.read_offline(pid, _DEV_USER_ID, operation='once')


def test_v10517_manifest_tampering_rejected_and_no_plaintext_fallback(monkeypatch):
    _, _, _, bucket, _, pid, writer, reader = setup(monkeypatch)
    result = writer.create(pid, _DEV_USER_ID, operation='modify')
    meta = bucket.stat(result.manifest_name)
    bucket.delete(result.manifest_name, if_generation_match=meta.generation)
    bucket.write_chunks(result.manifest_name, (b'{"format":"hosted-v3","plaintext":"secret"}',),
                        max_bytes=1024, content_type=meta.content_type,
                        if_generation_match=0, metadata=meta.metadata)
    with pytest.raises(ObjectIntegrityError):
        reader.read_offline(pid, _DEV_USER_ID, operation='modify')


def test_v10517_early_failure_leaves_uncommitted_chunks_only(monkeypatch):
    import hosted.encrypted_recovery as module
    runtime, _, _, bucket, _, pid, writer, reader = setup(monkeypatch)
    monkeypatch.setattr(module, 'CHUNK_BYTES', 1024)
    monkeypatch.setattr(HostedProjectPackageService, 'export_bytes', lambda *_: b'A' * 1600)
    original_write = bucket.write_chunks

    def deny_second_part(name, *args, **kwargs):
        if '/part_001.bme' in name:
            raise RuntimeError('GCS unavailable')
        return original_write(name, *args, **kwargs)

    monkeypatch.setattr(bucket, 'write_chunks', deny_second_part)
    with pytest.raises(RuntimeError, match='GCS unavailable'):
        writer.create(pid, _DEV_USER_ID, operation='partial')
    assert len(bucket.list_prefix(f'projects/{pid}/private/v1/recovery_chunk/')) == 1
    with pytest.raises(ObjectNotFound, match='HOSTED_ENCRYPTED_RECOVERY_UNCOMMITTED'):
        reader.read_offline(pid, _DEV_USER_ID, operation='partial')
    monkeypatch.setattr(bucket, 'write_chunks', original_write)
    finished = writer.create(pid, _DEV_USER_ID, operation='partial')
    assert finished.chunk_count == 2
    assert reader.read_offline(pid, _DEV_USER_ID, operation='partial') == b'A' * 1600


def test_v10517_concurrent_mutation_never_commits_manifest(monkeypatch):
    runtime, _, _, bucket, _, pid, writer, reader = setup(monkeypatch)
    original_export = HostedProjectPackageService.export_bytes

    def mutation(package, project_id, uid):
        data = original_export(package, project_id, uid)
        runtime.projects.rename(pid, 'Concurrent modification')
        return data

    monkeypatch.setattr(HostedProjectPackageService, 'export_bytes', mutation)
    with pytest.raises(DocumentConflict, match='HOSTED_ENCRYPTED_RECOVERY_CONCURRENT_MUTATION'):
        writer.create(pid, _DEV_USER_ID, operation='torn')
    with pytest.raises(ObjectNotFound):
        reader.read_offline(pid, _DEV_USER_ID, operation='torn')


def test_v10517_key_outage_does_not_fallback_to_unencrypted_gcs(monkeypatch):
    _, _, _, bucket, kms, pid, writer, _ = setup(monkeypatch)
    kms.enabled = False
    with pytest.raises(Exception) as err:
        writer.create(pid, _DEV_USER_ID, operation='kms_down')
    assert 'sensitive' not in str(err.value)
    assert not bucket.list_prefix(f'projects/{pid}/private/v1/')


def test_v10517_local_runtime_and_scheduled_plaintext_job_remain_unchanged():
    from hosted.encrypted_recovery import MAX_CHUNKS, CHUNK_BYTES
    assert MAX_CHUNKS * CHUNK_BYTES == 64 * 1024 * 1024
    assert 'hosted.encrypted_recovery' not in (ROOT / 'src/runtime/local.py').read_text()
    assert 'google-cloud-kms' not in (ROOT / 'requirements.txt').read_text()
    schedule = (ROOT / 'src/hosted/recovery_schedule.py').read_text()
    assert 'HostedEncryptedRecoveryWriter' not in schedule


def test_v10517_offline_restore_requires_retained_kms_version(monkeypatch):
    _, _, _, bucket, kms, pid, writer, reader = setup(monkeypatch)
    writer.create(pid, _DEV_USER_ID, operation='recover_after_outage')
    kms.enabled = False
    with pytest.raises(ObjectIntegrityError):
        reader.drill_offline(pid, _DEV_USER_ID, operation='recover_after_outage')
    assert bucket.list_prefix(f'projects/{pid}/private/v1/')
