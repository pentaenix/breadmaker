"""Portable imports through the actual account-erasure document/object facades."""
import pytest

from hosted.account_lifecycle import AccountLifecycleDocumentStore, AccountLifecycleObjectStore
from hosted.firestore import HostedProjectRepository, HostedStructuredRepository, HostedUserRepository, MemoryDocumentStore
from hosted.firestore.contracts import ProjectUnavailable, DocumentConflict
from hosted.packages import HostedProjectPackageService
from hosted.runtime_services import HostedCloudProjectRepository, ensure_hosted_user
from hosted.storage.memory import MemoryObjectStore
from hosted.idempotency import stable_project_id
from hosted.import_staging import package_import_staging
from tests.test_v10353 import OWNER
from tests.test_v10572 import highlighted


def fenced_service():
    raw, objects = MemoryDocumentStore(), MemoryObjectStore()
    documents = AccountLifecycleDocumentStore(raw, actor=lambda: OWNER)
    guarded = AccountLifecycleObjectStore(objects, documents)
    ensure_hosted_user(documents, user_id=OWNER, display_name='Disposable import tester')
    users = HostedUserRepository(HostedStructuredRepository(documents))
    projects = HostedCloudProjectRepository(documents, guarded, HostedProjectRepository(documents), users, lambda: OWNER)
    return HostedProjectPackageService(projects), raw, objects


def test_native_highlights_restore_through_production_erasure_fences():
    source, pid, _ = highlighted()
    payload = source.export_local_bytes(pid, OWNER)
    service, raw, _ = fenced_service()
    restored = service.import_bytes(payload, OWNER, operation='synthetic-fenced-native-import')
    rid = restored['id']
    assert rid != pid and service.projects.get(rid)['id'] == rid
    blocks = service.domain.list('manuscript_block', {'project_id': rid}, user_id=OWNER)
    assert [row['text_content'] for row in blocks] == ['She walked quietly and softly.']
    annotations = service.domain.list('annotation', {'project_id': rid}, user_id=OWNER)
    assert {row['selected_text'] for row in annotations} == {'quietly', 'softly'}
    assert not any(row['stale'] for row in annotations)
    assert len(raw.query_collection(('projects', rid, 'annotations'))) == 1


def test_hosted_v3_import_also_preserves_unpublished_fenced_staging():
    source, pid, _ = highlighted()
    service, _, _ = fenced_service()
    restored = service.import_bytes(source.export_bytes(pid, OWNER), OWNER)
    assert {row['selected_text'] for row in service.domain.list(
        'annotation', {'project_id': restored['id']}, user_id=OWNER)} == {'quietly', 'softly'}


def test_missing_project_writes_stay_denied_without_server_import_scope():
    service, _, objects = fenced_service()
    pid = 'a' * 32
    with pytest.raises(ProjectUnavailable):
        service.documents.run_transaction(lambda tx: tx.create(('projects', pid, 'chapters', 'one'), {}))
    with pytest.raises(ProjectUnavailable):
        service.objects.write_chunks(f'projects/{pid}/synthetic', [b'no'], max_bytes=2,
            content_type='text/plain', if_generation_match=0)
    assert not list(objects.list_prefix('projects/'))


def test_import_scope_requires_durable_same_actor_reservation_and_exact_prefix():
    service, raw, _ = fenced_service()
    op = 'synthetic-scope-proof'
    pid = stable_project_id(op)
    path = ('projects', pid, 'chapters', 'one')
    with package_import_staging(op, OWNER):
        with pytest.raises(DocumentConflict, match='RESERVATION_REQUIRED'):
            service.documents.run_transaction(lambda tx: tx.create(path, {}))
        service._reserve_import(operation=op, user_id=OWNER, digest='0'*64, name_override=None)
        with pytest.raises(ProjectUnavailable):
            service.documents.run_transaction(lambda tx: tx.create(('projects', 'b'*32, 'chapters', 'one'), {}))
        wrong_actor = AccountLifecycleDocumentStore(raw, actor=lambda: 'usr_'+'f'*32)
        with pytest.raises(DocumentConflict, match='ACTOR_MISMATCH'):
            wrong_actor.run_transaction(lambda tx: tx.create(path, {}))
    with pytest.raises(ProjectUnavailable):
        service.documents.run_transaction(lambda tx: tx.create(path, {}))


def test_interrupted_staging_remains_invisible_and_same_reserved_import_can_resume(monkeypatch):
    source, pid, _ = highlighted()
    payload = source.export_local_bytes(pid, OWNER)
    service, raw, _ = fenced_service()
    op = 'synthetic-interrupted-import'
    rid = stable_project_id(op)
    stage = service._stage_entity
    def interrupted(*args, **kwargs):
        stage(*args, **kwargs)
        assert raw.get(('projects', rid)) is None
        assert raw.query_collection(('projects', rid, 'members')) == []
        with pytest.raises(ProjectUnavailable):
            service.projects.authority.get_for_user(rid, OWNER)
        raise RuntimeError('synthetic interrupted transfer')
    monkeypatch.setattr(service, '_stage_entity', interrupted)
    with pytest.raises(RuntimeError, match='interrupted transfer'):
        service.import_bytes(payload, OWNER, operation=op)
    assert raw.get(('projects', rid)) is None
    with pytest.raises(ProjectUnavailable):
        service.documents.run_transaction(lambda tx: tx.create(('projects', rid, 'tasks', 'outside-scope'), {}))
    monkeypatch.setattr(service, '_stage_entity', stage)
    assert service.import_bytes(payload, OWNER, operation=op)['id'] == rid


def test_account_freeze_during_staging_blocks_next_child_write_and_publication(monkeypatch):
    source, pid, _ = highlighted()
    service, raw, _ = fenced_service()
    op = 'synthetic-frozen-import'
    rid = stable_project_id(op)
    stage = service._stage_entity
    def freeze_after_stage(*args, **kwargs):
        stage(*args, **kwargs)
        def freeze(tx):
            row = tx.get(('users', OWNER))
            tx.set(row.path, {**row.data, 'status': 'erasure_pending'})
        raw.run_transaction(freeze)
    monkeypatch.setattr(service, '_stage_entity', freeze_after_stage)
    with pytest.raises(DocumentConflict, match='ERASURE_PENDING'):
        service.import_bytes(source.export_local_bytes(pid, OWNER), OWNER, operation=op)
    assert raw.get(('projects', rid)) is None
    assert raw.query_collection(('projects', rid, 'members')) == []


def test_deleted_account_lock_blocks_staging_objects_and_documents():
    service, raw, objects = fenced_service()
    op = 'synthetic-deleted-owner'
    rid = stable_project_id(op)
    service._reserve_import(operation=op, user_id=OWNER, digest='0'*64, name_override=None)
    def close(tx):
        tx.delete(('users', OWNER))
        tx.create(('account_erasure_locks', OWNER), {'operation': 'synthetic-erasure'})
    raw.run_transaction(close)
    with package_import_staging(op, OWNER):
        with pytest.raises(DocumentConflict, match='ERASURE_PENDING'):
            service.documents.run_transaction(lambda tx: tx.create(('projects', rid, 'chapters', 'one'), {}))
        with pytest.raises(DocumentConflict, match='ERASURE_PENDING'):
            service.objects.write_chunks(f'projects/{rid}/synthetic', [b'no'], max_bytes=2,
                content_type='text/plain', if_generation_match=0)
    assert not list(objects.list_prefix('projects/'))


def test_erasure_freeze_before_final_publish_cannot_create_project_or_membership(monkeypatch):
    source, pid, _ = highlighted()
    service, raw, _ = fenced_service()
    op = 'synthetic-freeze-before-publish'
    rid = stable_project_id(op)
    finish = service._finish_import
    def freeze_then_finish(*args, **kwargs):
        def freeze(tx):
            row = tx.get(('users', OWNER))
            tx.set(row.path, {**row.data, '_bm_erasure_operation': 'synthetic-erasure'})
        raw.run_transaction(freeze)
        return finish(*args, **kwargs)
    monkeypatch.setattr(service, '_finish_import', freeze_then_finish)
    with pytest.raises(DocumentConflict, match='ERASURE_PENDING'):
        service.import_bytes(source.export_local_bytes(pid, OWNER), OWNER, operation=op)
    assert raw.get(('projects', rid)) is None
    assert raw.query_collection(('projects', rid, 'members')) == []


def test_import_owner_lock_is_rechecked_even_with_active_profile_at_final_publish(monkeypatch):
    source, pid, _ = highlighted()
    service, raw, _ = fenced_service()
    op = 'synthetic-lock-before-publish'
    rid = stable_project_id(op)
    finish = service._finish_import
    def lock_then_finish(*args, **kwargs):
        raw.run_transaction(lambda tx: tx.create(('account_erasure_locks', OWNER), {'operation': 'synthetic-erasure'}))
        return finish(*args, **kwargs)
    monkeypatch.setattr(service, '_finish_import', lock_then_finish)
    with pytest.raises(DocumentConflict, match='ERASURE_PENDING'):
        service.import_bytes(source.export_local_bytes(pid, OWNER), OWNER, operation=op)
    assert raw.get(('projects', rid)) is None
    assert raw.query_collection(('projects', rid, 'members')) == []


def test_upload_started_before_freeze_cannot_publish_its_pointer(monkeypatch):
    source, pid, _ = highlighted()
    service, raw, objects = fenced_service()
    op = 'synthetic-freeze-during-object'
    rid = stable_project_id(op)
    write = objects.write_chunks
    def write_then_freeze(*args, **kwargs):
        result = write(*args, **kwargs)
        def freeze(tx):
            row = tx.get(('users', OWNER))
            tx.set(row.path, {**row.data, 'status': 'erasure_pending'})
        raw.run_transaction(freeze)
        return result
    monkeypatch.setattr(objects, 'write_chunks', write_then_freeze)
    with pytest.raises(DocumentConflict, match='ERASURE_PENDING'):
        service.import_bytes(source.export_local_bytes(pid, OWNER), OWNER, operation=op)
    assert list(objects.list_prefix(f'projects/{rid}/'))  # In-flight upload may finish.
    assert raw.query_collection(('projects', rid, 'blocks')) == []
    assert raw.get(('projects', rid)) is None
    assert raw.query_collection(('projects', rid, 'members')) == []
