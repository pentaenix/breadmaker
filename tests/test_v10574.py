"""Recoverable project deletion retains data and current server authority."""
import pytest

from hosted.firestore.contracts import DocumentConflict, ProjectUnavailable
from hosted.runtime_services import HostedCommittedAssetStore, HostedHistoryStore, ensure_hosted_user
from tests.test_v10353 import OWNER, OUTSIDER, PNG
from tests.test_v10573 import fenced_service


def test_fenced_trash_restores_text_tasks_assets_history_and_provider_objects():
    service, raw, objects = fenced_service()
    projects = service.projects
    pid = projects.create('Disposable recoverable project')['id']
    domain = service.domain
    block = domain.get('manuscript_block', {'project_id': pid, 'block_id': 'b_1'}, user_id=OWNER)
    domain.update('manuscript_block', {'project_id': pid, 'block_id': block['id']},
        {'text_content': 'Synthetic retained writing', 'content_html': '<p>Synthetic retained writing</p>'}, user_id=OWNER)
    domain.create('task', {'project_id': pid}, {'title': 'Synthetic retained task'}, user_id=OWNER)
    assets = HostedCommittedAssetStore(service.documents, service.objects, lambda: OWNER, lambda: pid)
    asset = assets.put_blob(PNG, '.png')
    history = HostedHistoryStore(service.documents, service.objects, lambda: OWNER, lambda: pid)
    history.create_revision(b'synthetic-recovery-envelope', {'state_hash': 'a'*64})
    records = service._collect_authorized_records(pid, OWNER)
    recovery = history.list_revisions()
    inventory = list(objects.list_prefix(f'projects/{pid}/'))
    generation = raw.get(('projects', pid)).data['generation']
    projects.delete(pid)
    trash = projects.list_trash()
    assert len(trash) == 1 and trash[0]['trash_id'] == pid
    assert raw.get(('projects', pid)).data['status'] == 'deleted'
    assert raw.get(('projects', pid)).data['generation'] > generation
    with pytest.raises(ProjectUnavailable):
        domain.get('manuscript_block', {'project_id': pid, 'block_id': block['id']}, user_id=OWNER)
    assert list(objects.list_prefix(f'projects/{pid}/')) == inventory
    restored = projects.restore_from_trash(trash[0]['trash_id'])
    assert restored['id'] == pid and restored['status'] == 'active'
    assert projects.list_trash() == []
    assert service._collect_authorized_records(pid, OWNER) == records
    assert history.list_revisions() == recovery
    assert assets.read_blob(asset.api_path) == PNG
    assert list(objects.list_prefix(f'projects/{pid}/')) == inventory


def test_only_current_owner_can_trash_or_restore_project():
    service, raw, _ = fenced_service()
    projects = service.projects
    pid = projects.create('Disposable authority check')['id']
    editor = 'usr_' + 'e'*32
    for uid in (OUTSIDER, editor):
        ensure_hosted_user(service.documents, user_id=uid, display_name='Synthetic non-owner')
    projects.add_member(pid, editor, 'editor')
    service.documents.actor = lambda: projects.user_id_provider()
    before = dict(raw.get(('projects', pid)).data)
    for uid in (OUTSIDER, editor):
        projects.user_id_provider = lambda uid=uid: uid
        with pytest.raises(ProjectUnavailable):
            projects.delete(pid)
        assert dict(raw.get(('projects', pid)).data) == before
    projects.user_id_provider = lambda: OWNER
    projects.delete(pid)
    deleted = dict(raw.get(('projects', pid)).data)
    for uid in (OUTSIDER, editor):
        projects.user_id_provider = lambda uid=uid: uid
        with pytest.raises(ProjectUnavailable):
            projects.restore_from_trash(pid)
        assert dict(raw.get(('projects', pid)).data) == deleted
    projects.user_id_provider = lambda: OWNER
    assert projects.restore_from_trash(pid)['status'] == 'active'


def test_account_erasure_fence_still_blocks_project_trash_and_restore():
    service, raw, _ = fenced_service()
    projects = service.projects
    pid = projects.create('Disposable freeze check')['id']
    def freeze(tx):
        row = tx.get(('users', OWNER))
        tx.set(row.path, {**row.data, 'status': 'erasure_pending'})
    raw.run_transaction(freeze)
    with pytest.raises(DocumentConflict, match='ERASURE_PENDING'):
        projects.delete(pid)
    assert raw.get(('projects', pid)).data['status'] == 'active'
    def stage_deleted(tx):
        row = tx.get(('projects', pid))
        tx.set(row.path, {**row.data, 'status': 'deleted'})
    raw.run_transaction(stage_deleted)
    with pytest.raises(DocumentConflict, match='ERASURE_PENDING'):
        projects.restore_from_trash(pid)
    assert raw.get(('projects', pid)).data['status'] == 'deleted'
