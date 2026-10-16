"""M9A independent recovery archive conformance — for USER-OPERATED verifier only.

Never invokes real GCP, test runner, or the local data backend.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hosted.firestore.contracts import DocumentConflict, ProjectUnavailable, HostedAuthorizationError
from hosted.firestore.memory import MemoryDocumentStore
from hosted.recovery_archives import HostedRecoveryArchiveService
from hosted.runtime_services import _DEV_USER_ID, ensure_hosted_user
from hosted.storage.contracts import ObjectIntegrityError, ObjectLimitExceeded
from hosted.storage.memory import MemoryObjectStore
from runtime.hosted import build_hosted_dev_runtime


def _setup(monkeypatch):
    monkeypatch.setenv('BREADMAKER_AUTH_MODE', 'developer')
    monkeypatch.setenv('BREADMAKER_ENV', 'development')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED', '1')
    documents, content, recovery = MemoryDocumentStore(), MemoryObjectStore(), MemoryObjectStore()
    def replica():
        runtime = build_hosted_dev_runtime(documents=documents, objects=content)
        return runtime, HostedRecoveryArchiveService(runtime.projects, recovery)
    instance, service = replica()
    project_id = instance.projects.create('Only copy')['id']
    return replica, instance, service, documents, content, recovery, project_id


def test_v10366_archive_survives_new_runtime_instance_without_receipt(monkeypatch):
    replica, _, first, _, _, recovery, pid = _setup(monkeypatch)
    snapshot = first.create(pid, _DEV_USER_ID, operation='snapshot_fixed')
    assert snapshot.size > 0
    assert snapshot.object_name.startswith(f'recovery/v1/projects/{pid}/')
    assert len(recovery.list_prefix(f'recovery/v1/projects/{pid}/')) == 1
    _, second = replica()
    replay = second.create(pid, _DEV_USER_ID, operation='snapshot_fixed')
    assert replay == snapshot
    assert len(recovery.list_prefix(f'recovery/v1/projects/{pid}/')) == 1
    payload = second.read(pid, _DEV_USER_ID, operation='snapshot_fixed')
    assert hashlib.sha256(payload).hexdigest() == snapshot.sha256
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        entities = json.loads(archive.read('project.json'))['entities']
        assert manifest['source_project_id'] == pid
        assert manifest['format'] == 'breadmaker-hosted-project'
        assert entities['chapter'][0]['data']['title'] == 'Chapter 1'


def test_v10366_old_snapshot_unchanged_after_new_project_commit(monkeypatch):
    replica, instance, service, _, _, recovery, pid = _setup(monkeypatch)
    original = service.create(pid, _DEV_USER_ID, operation='first')
    instance.projects.rename(pid, 'Changed after the snapshot')
    _, second = replica()
    replay = second.create(pid, _DEV_USER_ID, operation='first')
    assert replay == original
    newer = second.create(pid, _DEV_USER_ID, operation='second')
    assert newer.project_generation > original.project_generation
    assert newer.object_name != original.object_name
    assert len(recovery.list_prefix(f'recovery/v1/projects/{pid}/')) == 2


def test_v10366_backup_requires_owner_and_live_project(monkeypatch):
    _, instance, service, documents, _, recovery, pid = _setup(monkeypatch)
    outsider = 'usr_' + '2' * 32
    with pytest.raises(ProjectUnavailable):
        service.create(pid, outsider, operation='attack')
    ensure_hosted_user(documents, user_id=outsider, display_name='Collaborator')
    expected = instance.projects.get(pid)['generation']
    instance.projects.authority.add_member(
        pid, _DEV_USER_ID, outsider, 'editor', expected_project_generation=expected,
        operation_id='m9a_editor_membership',
    )
    with pytest.raises(HostedAuthorizationError):
        service.create(pid, outsider, operation='attack')
    assert recovery.list_prefix(f'recovery/v1/projects/{pid}/') == []


def test_v10366_export_generation_fence_blocks_torn_archive(monkeypatch):
    _, instance, service, _, _, recovery, pid = _setup(monkeypatch)
    from hosted.packages import HostedProjectPackageService
    original = HostedProjectPackageService.export_bytes
    def concurrent_commit(package, project_id, user_id):
        payload = original(package, project_id, user_id)
        instance.projects.rename(pid, 'Concurrent writer')
        return payload
    monkeypatch.setattr(HostedProjectPackageService, 'export_bytes', concurrent_commit)
    with pytest.raises(DocumentConflict, match='HOSTED_RECOVERY_CONCURRENT_MUTATION'):
        service.create(pid, _DEV_USER_ID, operation='race')
    assert recovery.list_prefix(f'recovery/v1/projects/{pid}/') == []


def test_v10366_tampered_backup_is_not_accepted_on_replay(monkeypatch):
    _, _, service, _, _, recovery, pid = _setup(monkeypatch)
    record = service.create(pid, _DEV_USER_ID, operation='tamper')
    old = recovery.stat(record.object_name)
    recovery.delete(record.object_name, if_generation_match=old.generation)
    recovery.write_chunks(
        record.object_name, [b'not the archived manuscript'], max_bytes=128,
        content_type=old.content_type, if_generation_match=0, metadata=old.metadata,
    )
    with pytest.raises(ObjectIntegrityError, match='HOSTED_RECOVERY_OBJECT_HASH_MISMATCH'):
        service.create(pid, _DEV_USER_ID, operation='tamper')


def test_v10366_recovery_store_must_be_independent_and_bounded(monkeypatch):
    _, instance, _, _, content, recovery, pid = _setup(monkeypatch)
    with pytest.raises(ValueError, match='HOSTED_RECOVERY_INDEPENDENT_BUCKET_REQUIRED'):
        HostedRecoveryArchiveService(instance.projects, content)
    tiny = HostedRecoveryArchiveService(instance.projects, recovery, max_bytes=10)
    with pytest.raises(ObjectLimitExceeded, match='HOSTED_RECOVERY_ARCHIVE_TOO_LARGE'):
        tiny.create(pid, _DEV_USER_ID, operation='oversized')
    assert recovery.list_prefix(f'recovery/v1/projects/{pid}/') == []


def test_v10366_export_read_failure_must_not_publish_partial_recovery(monkeypatch):
    _, instance, service, _, _, recovery, pid = _setup(monkeypatch)
    original = instance.projects.domain.list
    def fail_list(entity, *args, **kwargs):
        if entity == 'chapter':
            raise RuntimeError('FIRESTORE_QUERY_INTERRUPTED')
        return original(entity, *args, **kwargs)
    monkeypatch.setattr(instance.projects.domain, 'list', fail_list)
    with pytest.raises(RuntimeError, match='FIRESTORE_QUERY_INTERRUPTED'):
        service.create(pid, _DEV_USER_ID, operation='fail_read')
    assert recovery.list_prefix(f'recovery/v1/projects/{pid}/') == []
