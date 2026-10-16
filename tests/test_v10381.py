"""M9C3 account closure rehearsal: written for operator verifier; not run by model."""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hosted.account_deletion import HostedAccountDeletionPlanner
from hosted.account_sandbox import HostedSandboxAccountClosure, SimulatedAccountCrash
from hosted.firestore.contracts import DocumentConflict, HostedAuthorizationError
from hosted.firestore.memory import MemoryDocumentStore
from hosted.runtime_services import _DEV_USER_ID, ensure_hosted_user
from hosted.storage.memory import MemoryObjectStore
from runtime.hosted import build_hosted_dev_runtime

UID = _DEV_USER_ID
OTHER = 'usr_' + '4' * 32
KEY = 'close-account-operation'


def _fixture(monkeypatch, *, collaborator=False, owned=False, objects=1):
    monkeypatch.setenv('BREADMAKER_AUTH_MODE', 'developer')
    monkeypatch.setenv('BREADMAKER_ENV', 'development')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED', '1')
    docs, content, recovery = MemoryDocumentStore(), MemoryObjectStore(), MemoryObjectStore()
    runtime = build_hosted_dev_runtime(documents=docs, objects=content)
    ensure_hosted_user(docs, user_id=UID, display_name='Account owner')
    ensure_hosted_user(docs, user_id=OTHER, display_name='Other writer')
    owned_id = runtime.projects.create('My book')['id'] if owned else None
    foreign = None
    if collaborator:
        # Create a foreign-owned project without swapping developer runtime actors.
        foreign = 'a' * 32
        docs.run_transaction(lambda tx: (
            tx.create(('projects', foreign), {'project_id': foreign, 'title': 'Other book', 'generation': 1}),
            tx.create(('projects', foreign, 'members', OTHER), {'role': 'owner'}),
            tx.create(('projects', foreign, 'members', UID), {'role': 'editor'}),
            tx.create(('projects', foreign, 'chapters', 'chapter_1'), {'body': 'Foreign manuscript'})))
    for i in range(objects):
        content.write_chunks(f'users/{UID}/avatars/avatar_{i}.png', [bytes([i])],
                             max_bytes=10, content_type='image/png', if_generation_match=0)
    content.write_chunks(f'users/{OTHER}/avatars/safe.png', [b'safe'],
                         max_bytes=10, content_type='image/png', if_generation_match=0)
    docs.run_transaction(lambda tx: tx.create(('identity_bindings', 'binding_1'), {
        'issuer': 'example', 'subject': '123', 'user_id': UID}))
    recovery.write_chunks('recovery/v1/projects/' + ('b' * 32) + '/sealed.breadmaker',
                          [b'independent recovery'], max_bytes=40,
                          content_type='application/octet-stream', if_generation_match=0)
    planner = HostedAccountDeletionPlanner(docs, content, recovery, lambda: UID)
    return docs, content, recovery, planner, foreign, owned_id


def _closure(planner, now=None):
    return HostedSandboxAccountClosure(planner, now=(lambda: now[0]) if now else None)


def _step(executor, **kwargs):
    return executor.step(UID, operation_key=KEY, worker_id='worker_one', **kwargs)


def test_v10381_arm_does_not_change_account_or_provider_data(monkeypatch):
    docs, content, recovery, planner, _, _ = _fixture(monkeypatch)
    before = {p: (dict(data), revision) for p, (data, revision) in docs._documents.items()}
    objs = tuple((x.name, x.generation, x.sha256) for x in content.list_prefix(f'users/{UID}/'))
    armed = _closure(planner).arm(UID, operation_key=KEY)
    assert armed.state == 'sandbox_armed' and armed.cursor == 0
    assert armed.external_identity_revocation_required
    assert not armed.production_account_deletion_authorized
    assert all(docs._documents[p] == original for p, original in before.items())
    assert tuple((x.name, x.generation, x.sha256) for x in content.list_prefix(f'users/{UID}/')) == objs
    assert _closure(planner).arm(UID, operation_key=KEY) == armed


def test_v10381_complete_data_cleanup_leaves_identity_gate_and_sealed_backup(monkeypatch):
    docs, content, recovery, planner, foreign, _ = _fixture(monkeypatch, collaborator=True, objects=2)
    archived = next(iter(recovery.list_prefix('recovery/')))
    initial_foreign = docs.get(('projects', foreign, 'chapters', 'chapter_1'))
    start = _closure(planner).arm(UID, operation_key=KEY)
    for index in range(start.total):
        progress = _step(_closure(planner))
        assert progress.cursor == index + 1
        assert not progress.production_account_deletion_authorized
    assert progress.state == 'sandbox_identity_revocation_pending'
    assert docs.get(('users', UID)) is None
    assert docs.get(('identity_bindings', 'binding_1')) is None
    assert docs.get(('projects', foreign, 'members', UID)) is None
    assert docs.get(('projects', foreign, 'members', OTHER)) is not None
    assert docs.get(('projects', foreign, 'chapters', 'chapter_1')) == initial_foreign
    assert docs.get(('users', OTHER)) is not None
    assert not content.list_prefix(f'users/{UID}/')
    assert content.list_prefix(f'users/{OTHER}/')
    assert recovery.stat(archived.name) is not None
    assert _step(_closure(planner)) == progress
    assert docs.get(('account_sandbox_closures', UID)) is not None


def test_v10381_live_owner_project_blocks_erasure(monkeypatch):
    docs, content, recovery, planner, _, owned = _fixture(monkeypatch, owned=True)
    with pytest.raises(DocumentConflict, match='PROJECTS_OR_UNKNOWN_REFERENCES'):
        _closure(planner).arm(UID, operation_key=KEY)
    assert docs.get(('projects', owned)) is not None
    assert docs.get(('users', UID)) is not None


def test_v10381_unknown_user_reference_fails_closed(monkeypatch):
    docs, content, recovery, planner, _, _ = _fixture(monkeypatch)
    docs.run_transaction(lambda tx: tx.create(('future_global', 'unknown'), {'user_id': UID}))
    with pytest.raises(DocumentConflict, match='PROJECTS_OR_UNKNOWN_REFERENCES'):
        _closure(planner).arm(UID, operation_key=KEY)
    assert docs.get(('future_global', 'unknown')) is not None


def test_v10381_orphan_membership_does_not_disappear(monkeypatch):
    docs, content, recovery, planner, foreign, _ = _fixture(monkeypatch, collaborator=True)
    docs.run_transaction(lambda tx: tx.delete(('projects', foreign)))
    with pytest.raises(DocumentConflict, match='PROJECTS_OR_UNKNOWN_REFERENCES'):
        _closure(planner).arm(UID, operation_key=KEY)


def test_v10381_changed_user_doc_or_new_reference_aborts(monkeypatch):
    docs, content, recovery, planner, _, _ = _fixture(monkeypatch, objects=0)
    _closure(planner).arm(UID, operation_key=KEY)
    def change(tx):
        profile = tx.get(('users', UID))
        tx.set(('users', UID), {**profile.data, 'display_name': 'changed'})
    docs.run_transaction(change)
    with pytest.raises(DocumentConflict, match='DOCUMENT_DRIFT'):
        _step(_closure(planner))
    assert docs.get(('users', UID)) is not None


def test_v10381_foreign_project_revision_change_stops_detachment(monkeypatch):
    docs, content, recovery, planner, foreign, _ = _fixture(monkeypatch, collaborator=True, objects=0)
    _closure(planner).arm(UID, operation_key=KEY)
    def change(tx):
        root = tx.get(('projects', foreign))
        tx.set(('projects', foreign), {**root.data, 'title': 'new'})
    docs.run_transaction(change)
    with pytest.raises(DocumentConflict, match='FOREIGN_PROJECT_DRIFT'):
        _step(_closure(planner))
    assert docs.get(('projects', foreign, 'members', UID)) is not None


def test_v10381_changed_object_generation_is_not_deleted(monkeypatch):
    docs, content, recovery, planner, _, _ = _fixture(monkeypatch)
    _closure(planner).arm(UID, operation_key=KEY)
    original = content.list_prefix(f'users/{UID}/')[0]
    content.write_chunks(original.name, [b'new'], max_bytes=10,
                         content_type='image/png', if_generation_match=original.generation)
    with pytest.raises(DocumentConflict, match='OBJECT_DRIFT'):
        _step(_closure(planner))
    assert content.stat(original.name).sha256 != original.sha256


def test_v10381_crash_replay_and_worker_lease(monkeypatch):
    docs, content, recovery, planner, _, _ = _fixture(monkeypatch)
    clock = [datetime(2026, 10, 12, tzinfo=timezone.utc)]
    _closure(planner, clock).arm(UID, operation_key=KEY)
    with pytest.raises(SimulatedAccountCrash):
        _step(_closure(planner, clock), crash_after_object_delete=True)
    assert not content.list_prefix(f'users/{UID}/')
    with pytest.raises(DocumentConflict, match='LEASE_HELD'):
        _closure(planner, clock).step(UID, operation_key=KEY, worker_id='another_worker')
    clock[0] += timedelta(seconds=31)
    progressed = _closure(planner, clock).step(UID, operation_key=KEY, worker_id='another_worker')
    assert progressed.cursor == 1
    assert _step(_closure(planner, clock)).cursor == 2


def test_v10381_new_object_after_arm_blocks_step(monkeypatch):
    docs, content, recovery, planner, _, _ = _fixture(monkeypatch, objects=0)
    _closure(planner).arm(UID, operation_key=KEY)
    content.write_chunks(f'users/{UID}/avatars/new.png', [b'x'], max_bytes=5,
                         content_type='image/png', if_generation_match=0)
    with pytest.raises(DocumentConflict, match='OBJECT_DRIFT'):
        _step(_closure(planner))


def test_v10381_self_only_and_conflicting_key(monkeypatch):
    docs, content, recovery, planner, _, _ = _fixture(monkeypatch)
    with pytest.raises(HostedAuthorizationError, match='SELF_ONLY'):
        _closure(planner).arm(OTHER, operation_key=KEY)
    _closure(planner).arm(UID, operation_key=KEY)
    with pytest.raises(DocumentConflict, match='RECEIPT_CONFLICT'):
        _closure(planner).arm(UID, operation_key='different_key')


def test_v10381_exact_memory_providers_only_and_no_identity_revocation(monkeypatch):
    docs, content, recovery, planner, _, _ = _fixture(monkeypatch)
    assert not hasattr(HostedSandboxAccountClosure, 'delete_provider_identity')
    assert not hasattr(HostedSandboxAccountClosure, 'revoke')
    assert not hasattr(HostedSandboxAccountClosure, 'execute_gcp')
    assert _closure(planner)
    with pytest.raises(ValueError, match='MEMORY_ONLY'):
        fake = HostedAccountDeletionPlanner(docs, content, recovery, lambda: UID)
        fake.documents = object()
        HostedSandboxAccountClosure(fake)


def test_v10381_account_docs_transaction_retry_does_not_skip_cursor(monkeypatch):
    docs, content, recovery, planner, _, _ = _fixture(monkeypatch, objects=0)
    start = _closure(planner).arm(UID, operation_key=KEY)
    docs.retry_next_transaction()
    result = _step(_closure(planner))
    assert result.cursor == 1 and result.total == start.total


def test_v10381_production_gate_is_still_hard_denial_and_http_has_no_erase_route():
    from hosted.purge_production_gate import HostedProductionPurgeGate, REQUIRED_PROOFS
    assert not HostedProductionPurgeGate.assess(recorded_proofs=frozenset(REQUIRED_PROOFS)).production_deletion_authorized
    root = Path(__file__).resolve().parents[1]
    api = (root / 'src/hosted/http_api.py').read_text(encoding='utf-8')
    assert 'HostedSandboxAccountClosure' not in api
    assert 'HostedSandboxPurgeExecutor' not in api


def test_v10381_unknown_nested_user_document_requires_explicit_retention_policy(monkeypatch):
    docs, content, recovery, planner, _, _ = _fixture(monkeypatch, objects=0)
    docs.run_transaction(lambda tx: tx.create(('users', UID, 'future', 'setting_1'), {'value': 1}))
    assert 'HOSTED_ACCOUNT_UNCLASSIFIED_USER_REFERENCES' in planner.preview(UID).blockers
    with pytest.raises(DocumentConflict, match='PROJECTS_OR_UNKNOWN_REFERENCES'):
        _closure(planner).arm(UID, operation_key=KEY)


def test_v10381_unregistered_actor_or_owner_reference_is_not_silently_erased(monkeypatch):
    docs, content, recovery, planner, _, _ = _fixture(monkeypatch, objects=0)
    docs.run_transaction(lambda tx: tx.create(('future_audit', 'row_1'), {'actor_user_id': UID}))
    assert 'HOSTED_ACCOUNT_UNCLASSIFIED_USER_REFERENCES' in planner.preview(UID).blockers
    with pytest.raises(DocumentConflict, match='PROJECTS_OR_UNKNOWN_REFERENCES'):
        _closure(planner).arm(UID, operation_key=KEY)


def test_v10381_project_purge_receipts_survive_and_enable_subsequent_account_rehearsal(monkeypatch):
    from hosted.purge_discovery import MemoryProjectTreeDiscovery
    from hosted.purge_planning import HostedPurgePlanner
    from hosted.purge_receipts import HostedPurgePreparationService
    from hosted.purge_sandbox import HostedSandboxPurgeExecutor
    from hosted.recovery_archives import HostedRecoveryArchiveService

    docs, content, recovery, account_planner, _, _ = _fixture(monkeypatch, objects=0)
    runtime = build_hosted_dev_runtime(documents=docs, objects=content)
    pid = runtime.projects.create('Owned novel')['id']
    original_archive = HostedRecoveryArchiveService(runtime.projects, recovery).create(
        pid, UID, operation='before_erase')
    with pytest.raises(DocumentConflict, match='PROJECTS_OR_UNKNOWN_REFERENCES'):
        _closure(account_planner).arm(UID, operation_key=KEY)
    runtime.projects.delete(pid)
    deleted = docs.get(('projects', pid))
    tombstone = datetime.fromisoformat(deleted.data['deleted_at'].replace('Z', '+00:00'))
    clock = [tombstone + timedelta(days=31)]
    purge_planner = HostedPurgePlanner(runtime.projects, recovery,
        discovery=MemoryProjectTreeDiscovery(docs), now=lambda: clock[0])
    preparation = HostedPurgePreparationService(purge_planner)
    purger = HostedSandboxPurgeExecutor(preparation, now=lambda: clock[0])
    pending = purger.arm(pid, UID, operation_key='project-purge')
    for _ in range(pending.total):
        pending = purger.step(pid, UID, operation_key='project-purge', worker_id='purge_worker')
    assert pending.state == 'sandbox_completed'
    assert docs.get(('projects', pid)) is None
    assert recovery.stat(original_archive.object_name) is not None
    account_preview = account_planner.preview(UID)
    assert not account_preview.owned_projects
    assert len(account_preview.retained_purge_audit_paths) == 2
    armed = _closure(account_planner).arm(UID, operation_key=KEY)
    for _ in range(armed.total):
        armed = _step(_closure(account_planner))
    assert armed.state == 'sandbox_identity_revocation_pending'
    assert docs.get(('purge_preparations', pid)) is not None
    assert docs.get(('purge_sandbox_executions', pid)) is not None
    assert recovery.stat(original_archive.object_name) is not None


def test_v10381_tampered_or_new_audit_receipt_blocks_account_progress(monkeypatch):
    docs, content, recovery, planner, _, _ = _fixture(monkeypatch, objects=0)
    _closure(planner).arm(UID, operation_key=KEY)
    docs.run_transaction(lambda tx: tx.create(('purge_preparations', 'a' * 32), {
        'project_id': 'a' * 32, 'owner_user_id': UID, 'physical_purge_authorized': False,
        'state': 'prepared_read_only'}))
    with pytest.raises(DocumentConflict, match='AUDIT_DRIFT'):
        _step(_closure(planner))


def test_v10381_rehearsal_receipt_is_read_only_audit_not_unknown_account_data(monkeypatch):
    docs, content, recovery, planner, _, _ = _fixture(monkeypatch, objects=0)
    _closure(planner).arm(UID, operation_key=KEY)
    snapshot = planner.preview(UID)
    assert ('account_sandbox_closures', UID) in snapshot.retained_purge_audit_paths
    assert 'HOSTED_ACCOUNT_UNCLASSIFIED_USER_REFERENCES' not in snapshot.blockers
    assert docs.get(('account_sandbox_closures', UID)) is not None
