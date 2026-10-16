"""M9 deletion Bundle 2 foundations. User verifier runs; model never does."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hosted.account_deletion import HostedAccountDeletionPlanner
from hosted.purge_production_gate import HostedProductionPurgeGate, REQUIRED_PROOFS
from hosted.firestore.contracts import DocumentConflict, HostedAuthorizationError
from hosted.firestore.memory import MemoryDocumentStore
from hosted.runtime_services import _DEV_USER_ID, ensure_hosted_user
from hosted.storage.memory import MemoryObjectStore
from runtime.hosted import build_hosted_dev_runtime


def _fixture(monkeypatch):
    monkeypatch.setenv('BREADMAKER_AUTH_MODE','developer')
    monkeypatch.setenv('BREADMAKER_ENV','development')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED','1')
    docs, content, recovery = MemoryDocumentStore(), MemoryObjectStore(), MemoryObjectStore()
    runtime = build_hosted_dev_runtime(documents=docs, objects=content)
    pid = runtime.projects.create('My manuscript')['id']
    planner = HostedAccountDeletionPlanner(docs, content, recovery, lambda: _DEV_USER_ID)
    return runtime, docs, content, recovery, pid, planner


def test_v10379_owner_project_preflight_never_authorizes_deletion(monkeypatch):
    runtime, docs, content, recovery, pid, planner = _fixture(monkeypatch)
    before = docs.get(('projects',pid)).data
    result = planner.preview(_DEV_USER_ID)
    assert result.owned_projects == (pid,)
    assert not result.physical_deletion_authorized
    assert 'HOSTED_ACCOUNT_OWNED_PROJECT_RETENTION_AND_PURGE_REQUIRED' in result.blockers
    assert result.public()['physical_deletion_authorized'] is False
    assert docs.get(('projects',pid)).data == before


def test_v10379_self_only_blocks_other_user_even_when_exists(monkeypatch):
    runtime, docs, content, recovery, pid, planner = _fixture(monkeypatch)
    second = 'usr_' + '4'*32
    ensure_hosted_user(docs, user_id=second, display_name='Second writer')
    with pytest.raises(HostedAuthorizationError,match='HOSTED_ACCOUNT_SELF_ONLY'):
        planner.preview(second)
    assert planner.preview(_DEV_USER_ID).owned_projects == (pid,)


def test_v10379_collaboration_preserves_other_writers_project(monkeypatch):
    runtime, docs, content, recovery, pid, planner = _fixture(monkeypatch)
    collaborator = 'usr_' + '4'*32
    ensure_hosted_user(docs,user_id=collaborator,display_name='Collaborator')
    runtime.projects.authority.add_member(pid,_DEV_USER_ID,collaborator,'editor',
        expected_project_generation=int(runtime.projects.get(pid)['generation']),
        operation_id='m9c3_add_collaborator')
    other = HostedAccountDeletionPlanner(docs,content,recovery,lambda:collaborator)
    result=other.preview(collaborator)
    assert result.owned_projects == () and result.collaborative_projects == (pid,)
    assert 'HOSTED_ACCOUNT_COLLABORATION_DETACH_REQUIRED' in result.blockers
    assert docs.get(('projects',pid)) is not None


def test_v10379_orphan_membership_discovered_not_ignored(monkeypatch):
    runtime,docs,content,recovery,pid,planner=_fixture(monkeypatch)
    docs.run_transaction(lambda tx: tx.delete(('projects',pid)))
    result=planner.preview(_DEV_USER_ID)
    assert pid in result.missing_project_roots
    assert 'HOSTED_ACCOUNT_ORPHANED_PROJECT_MEMBERSHIP' in result.blockers


def test_v10379_bindings_receipts_and_unknown_user_documents_fail_closed(monkeypatch):
    runtime,docs,content,recovery,pid,planner=_fixture(monkeypatch)
    docs.run_transaction(lambda tx: (
        tx.create(('identity_bindings','binding_one'),{'user_id':_DEV_USER_ID,'issuer':'test'}),
        tx.create(('hosted_import_operations','import_one'),{'user_id':_DEV_USER_ID}),
        tx.create(('some_future_account_records','record_one'),{'user_id':_DEV_USER_ID})))
    result=planner.preview(_DEV_USER_ID)
    assert result.identity_binding_paths == (('identity_bindings','binding_one'),)
    assert ('hosted_import_operations','import_one') in result.account_document_paths
    assert 'HOSTED_ACCOUNT_UNCLASSIFIED_USER_REFERENCES' in result.blockers
    assert docs.get(('some_future_account_records','record_one')) is not None


def test_v10379_fingerprint_changes_on_nested_revision_without_new_path(monkeypatch):
    runtime,docs,content,recovery,pid,planner=_fixture(monkeypatch)
    initial=planner.preview(_DEV_USER_ID)
    membership=('projects',pid,'members',_DEV_USER_ID)
    def edit(tx):
        current=tx.get(membership)
        updated=dict(current.data);updated['last_opened_at']='changed'
        tx.set(membership,updated)
    docs.run_transaction(edit)
    after=planner.preview(_DEV_USER_ID)
    assert initial.fingerprint != after.fingerprint
    assert initial.owned_projects == after.owned_projects


def test_v10379_avatar_and_recovery_prefix_scoped_by_user_and_project(monkeypatch):
    runtime,docs,content,recovery,pid,planner=_fixture(monkeypatch)
    other='usr_'+'4'*32
    ensure_hosted_user(docs,user_id=other,display_name='Unrelated')
    content.write_chunks(f'users/{_DEV_USER_ID}/avatars/avatar_test.webp',[b'x'],max_bytes=10,
                         content_type='image/webp',if_generation_match=0)
    content.write_chunks(f'users/{other}/avatars/other.webp',[b'y'],max_bytes=10,
                         content_type='image/webp',if_generation_match=0)
    recovery.write_chunks(f'recovery/v1/projects/{pid}/backup.bin',[b'safe'],max_bytes=10,
                          content_type='application/octet-stream',if_generation_match=0)
    result=planner.preview(_DEV_USER_ID)
    assert result.account_object_names==(f'users/{_DEV_USER_ID}/avatars/avatar_test.webp',)
    assert result.independent_recovery_archive_count == 1
    assert recovery.list_prefix(f'recovery/v1/projects/{pid}/')
    assert content.stat(f'users/{other}/avatars/other.webp') is not None


def test_v10379_memory_only_and_distinct_recovery_storage(monkeypatch):
    runtime,docs,content,recovery,pid,planner=_fixture(monkeypatch)
    with pytest.raises(ValueError, match='HOSTED_ACCOUNT_DISCOVERY_MEMORY_ONLY'):
        HostedAccountDeletionPlanner(docs,content,content,lambda:_DEV_USER_ID)
    with pytest.raises(ValueError, match='HOSTED_ACCOUNT_DISCOVERY_MEMORY_ONLY'):
        HostedAccountDeletionPlanner(object(),content,recovery,lambda:_DEV_USER_ID)


def test_v10379_production_purge_gate_never_grants_authority():
    empty=HostedProductionPurgeGate.assess()
    assert set(empty.missing_proofs) == set(REQUIRED_PROOFS)
    assert empty.production_deletion_authorized is False
    forged=HostedProductionPurgeGate.assess(recorded_proofs=frozenset(REQUIRED_PROOFS))
    assert forged.missing_proofs == ()
    assert forged.production_deletion_authorized is False and forged.provider_ready is False
    with pytest.raises(ValueError,match='HOSTED_PURGE_EVIDENCE_INVALID'):
        HostedProductionPurgeGate.assess(recorded_proofs={'fake'})


def test_v10379_account_planner_has_no_destructive_methods(monkeypatch):
    runtime,docs,content,recovery,pid,planner=_fixture(monkeypatch)
    assert not hasattr(planner,'delete')
    assert not hasattr(planner,'purge')
    assert not hasattr(planner,'erase')
    assert not hasattr(HostedProductionPurgeGate(),'execute')


def test_v10379_unrelated_profile_revisions_do_not_change_other_account_fingerprint(monkeypatch):
    runtime,docs,content,recovery,pid,planner=_fixture(monkeypatch)
    other='usr_'+'5'*32
    ensure_hosted_user(docs,user_id=other,display_name='Other writer')
    first=planner.preview(_DEV_USER_ID)
    def edit(tx):
        old=tx.get(('users',other))
        new=dict(old.data);new['display_name']='Updated other writer'
        tx.set(('users',other),new)
    docs.run_transaction(edit)
    assert planner.preview(_DEV_USER_ID).fingerprint==first.fingerprint


def test_v10379_unknown_account_keyed_global_document_fails_closed(monkeypatch):
    runtime,docs,content,recovery,pid,planner=_fixture(monkeypatch)
    path=('future_account_settings',_DEV_USER_ID)
    docs.run_transaction(lambda tx: tx.create(path,{'setting':'unknown'}))
    result=planner.preview(_DEV_USER_ID)
    assert path in result.account_document_paths
    assert 'HOSTED_ACCOUNT_UNCLASSIFIED_USER_REFERENCES' in result.blockers
