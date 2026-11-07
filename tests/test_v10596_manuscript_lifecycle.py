"""Root-only operator compatibility; synthetic fixtures, no runtime activation."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from hosted.account_closure import StagedAccountClosure
from hosted.account_lifecycle import AccountLifecycleDocumentStore, AccountLifecycleObjectStore
from hosted.firestore.contracts import DocumentConflict, HostedValidationError, ProjectUnavailable
from hosted.manuscript_lifecycle import read_project_lifecycle_status
from hosted.manuscript_visibility import FORMAT_FIELD, REVISION_FIELD, ManuscriptDocumentStore
from hosted.narration.gc_job import retention_allows
from hosted.recovery_coverage import physical_project_fingerprint
from tests.test_v10563_closure import EMAIL, EXTERNAL, OPERATION, PROJECT, close, fixture


INVALID_ROOTS = [
    {'status': 'active_manuscript_v1'},
    {'status': 'deleted_manuscript_v1'},
    {'status': 'active_manuscript_v1', FORMAT_FIELD: None},
    {'status': 'active_manuscript_v1', FORMAT_FIELD: True},
    {'status': 'active_manuscript_v1', FORMAT_FIELD: 1.0},
    {'status': 'active_manuscript_v1', FORMAT_FIELD: 2},
    {'status': 'active', FORMAT_FIELD: 1},
    {'status': 'deleted', FORMAT_FIELD: 1},
    {'status': 'unknown', FORMAT_FIELD: 1},
    {FORMAT_FIELD: 1},
]


def format_root(world, *, status='active_manuscript_v1', changes=None):
    path = ('projects', world.pids[0])
    data = {**world.docs.get(path).data, 'status': status, FORMAT_FIELD: 1}
    if changes is not None:
        data.pop('status', None)
        data.pop(FORMAT_FIELD, None)
        data.update(changes)
    world.docs.run_transaction(lambda tx: tx.set(path, data))
    return path


@pytest.mark.parametrize('physical,logical', [
    ('active_manuscript_v1', 'active'), ('deleted_manuscript_v1', 'deleted'),
    ('erasure_pending', 'erasure_pending'),
])
def test_root_codec_keeps_physical_inventory_and_revision_fingerprint(physical, logical):
    w = fixture(projects=1)
    root = format_root(w, status=physical)
    staged = root + ('chapters', 'staged')
    w.docs.run_transaction(lambda tx: tx.create(staged, {REVISION_FIELD: 'staged_revision'}))
    w.docs.run_transaction(lambda tx: tx.set(root, {**tx.get(root).data, '_bm_erasure_operation': 'retained_marker'}))
    before = deepcopy(w.docs.project_document_inventory(w.pids[0]))
    fingerprint = physical_project_fingerprint(w.docs, w.pids[0])
    row = w.docs.get(root)
    assert read_project_lifecycle_status(row) == logical
    assert row.data['status'] == physical and row.data[FORMAT_FIELD] == 1
    assert row.data['_bm_erasure_operation'] == 'retained_marker'
    assert w.docs.project_document_inventory(w.pids[0]) == before
    assert staged in {item.path for item in before}
    assert physical_project_fingerprint(w.docs, w.pids[0]) == fingerprint


@pytest.mark.parametrize('changes', INVALID_ROOTS)
def test_unknown_or_mismatched_format_fails_before_account_freeze(changes):
    w = fixture(projects=1)
    root = format_root(w, changes=changes)
    before = deepcopy(w.docs._documents)
    with pytest.raises(HostedValidationError):
        read_project_lifecycle_status(w.docs.get(root), default='active')
    with pytest.raises(DocumentConflict, match='ACCOUNT_ERASURE_PROJECT_CHANGED'):
        close(w)
    assert w.docs._documents == before and not w.identities.events
    assert not w.audit.list_prefix('erasure-audit/')


@pytest.mark.parametrize('status', ['active_manuscript_v1', 'deleted_manuscript_v1'])
def test_closure_freezes_known_roots_without_losing_hidden_physical_children(status):
    w = fixture(projects=1)
    root = format_root(w, status=status)
    staged = root + ('chapters', 'staged')
    orphan = root + ('unknown', 'missing', 'nested', 'retained')
    w.docs.run_transaction(lambda tx: tx.create(staged, {REVISION_FIELD: 'staged_revision'}))
    w.docs.run_transaction(lambda tx: tx.create(orphan, {'retained': True}))
    before = {row.path: row for row in w.docs.project_document_inventory(w.pids[0])}
    confirmation = w.engine.plan(EMAIL, operation=OPERATION).confirmation
    result = close(w, confirmation)
    after = {row.path: row for row in w.docs.project_document_inventory(w.pids[0])}
    assert result['account_closed'] and not result['physical_purge_authorized']
    assert set(after) == set(before)
    assert all(after[path] == row for path, row in before.items() if path != root)
    assert after[root].data['status'] == 'erasure_pending'
    assert after[root].data[FORMAT_FIELD] == 1
    assert after[root].data['_bm_erasure_operation'] == result['request_id']
    assert after[root].data['generation'] == before[root].data['generation'] + 1
    assert close(w, confirmation) == result


@pytest.mark.parametrize('actor', [True, False])
def test_normal_retained_and_object_writes_remain_fenced_after_format_closure(actor):
    w = fixture(projects=1)
    root = format_root(w)
    hidden = root + ('chapters', 'hidden')
    w.docs.run_transaction(lambda tx: tx.create(hidden, {REVISION_FIELD: 'retired_revision'}))
    fence = AccountLifecycleDocumentStore(w.docs, actor=lambda: w.uid if actor else None)
    view = ManuscriptDocumentStore(fence)
    uploads = AccountLifecycleObjectStore(w.live, fence)
    close(w)
    before = deepcopy(w.docs._documents)
    attempts = [
        lambda tx: tx.create(root + ('tabs', 'late'), {'title': 'late'}),
        lambda tx: tx.set(root, {**tx.get(root).data, 'status': 'active'}),
    ]
    for write in attempts:
        with pytest.raises((DocumentConflict, ProjectUnavailable)):
            view.run_transaction(write)
    with pytest.raises((DocumentConflict, ProjectUnavailable)):
        view.retained_view().run_transaction(lambda tx: tx.set(hidden, {**tx.get(hidden).data, 'late': True}))
    with pytest.raises((DocumentConflict, ProjectUnavailable)):
        uploads.write_chunks(f'projects/{w.pids[0]}/late', [b'no'], max_bytes=2,
            content_type='text/plain', if_generation_match=0)
    assert w.docs._documents == before
    assert w.live.stat(f'projects/{w.pids[0]}/late') is None


@pytest.mark.parametrize('changed', [
    {'status': 'active_manuscript_v1', FORMAT_FIELD: 1},
    {'status': 'erasure_pending', FORMAT_FIELD: 2},
])
def test_replayed_project_freeze_requires_exact_known_erasure_state(changed):
    def crash(state):
        if state == 'project_fenced':
            raise RuntimeError('stop after freeze')
    w = fixture(projects=1, checkpoint=crash)
    root = format_root(w)
    confirmation = w.engine.plan(EMAIL, operation=OPERATION).confirmation
    with pytest.raises(RuntimeError, match='stop after freeze'):
        close(w, confirmation)
    original = dict(w.docs.get(root).data)
    w.docs.run_transaction(lambda tx: tx.set(root, {**original, **changed}))
    w.engine = StagedAccountClosure(w.docs, w.identities, w.audit, project=PROJECT,
        now=lambda: datetime(2026, 10, 28, 12, tzinfo=timezone.utc))
    with pytest.raises(DocumentConflict, match='ACCOUNT_ERASURE_PROJECT_CHANGED'):
        close(w, confirmation)
    assert not w.identities.events
    assert w.docs.get(w.grant_path).data['bound_uid'] == EXTERNAL
    w.docs.run_transaction(lambda tx: tx.set(root, original))
    assert close(w, confirmation)['account_closed']


@pytest.mark.parametrize('changes,expected', [
    ({'status': 'active_manuscript_v1', FORMAT_FIELD: 1}, True),
    ({'status': 'deleted_manuscript_v1', FORMAT_FIELD: 1, 'deleted_at': '2026-10-01T00:00:00Z'}, True),
    ({'status': 'deleted_manuscript_v1', FORMAT_FIELD: 1, 'deleted_at': '2033-05-18T00:00:00Z'}, False),
    ({'status': 'active_manuscript_v1', FORMAT_FIELD: 1, 'legal_hold': True}, False),
    ({'status': 'active_manuscript_v1', FORMAT_FIELD: 1, 'narration_hold': True}, False),
    ({'status': 'active_manuscript_v1', FORMAT_FIELD: 1, '_bm_erasure_operation': 'unconfirmed'}, False),
    *[(changes, False) for changes in INVALID_ROOTS],
])
def test_narration_retention_checks_known_root_format_without_changing_inventory(changes, expected):
    w = fixture(projects=1)
    format_root(w, changes=changes)
    before = deepcopy(w.docs._documents)
    assert w.docs.run_transaction(lambda tx: retention_allows(tx, w.pids[0], w.uid, now=2_000_000_000)) is expected
    assert w.docs._documents == before


def test_format_erasure_remains_held_without_exact_purge_authority():
    w = fixture(projects=1)
    root = format_root(w)
    result = close(w)
    allowed = lambda: w.docs.run_transaction(lambda tx: retention_allows(tx, w.pids[0], w.uid, now=2_000_000_000))
    assert not allowed()
    closure = ('account_erasures', result['request_id'])
    w.docs.run_transaction(lambda tx: tx.set(closure, {**tx.get(closure).data, 'physical_purge_authorized': True}))
    assert allowed()
    frozen = dict(w.docs.get(root).data)
    for changes in ({'status': 'active_manuscript_v1'}, {FORMAT_FIELD: 2}, {'_bm_erasure_operation': 'wrong'}):
        w.docs.run_transaction(lambda tx: tx.set(root, {**frozen, **changes}))
        assert not allowed()


@pytest.mark.parametrize('changes', [
    {'status': 'active_manuscript_v1', FORMAT_FIELD: 2},
    {'status': 'active', FORMAT_FIELD: 1},
    {'status': 'active_manuscript_v1', FORMAT_FIELD: 1, '_bm_erasure_operation': 'foreign_operation'},
])
def test_project_freeze_rechecks_root_after_account_fence(changes):
    w = fixture(projects=1)
    root = format_root(w)
    confirmation = w.engine.plan(EMAIL, operation=OPERATION).confirmation
    def change_after_account_fence(state):
        if state == 'account_fenced':
            w.docs.run_transaction(lambda tx: tx.set(root, {**tx.get(root).data, **changes}))
    w.engine.checkpoint = change_after_account_fence
    with pytest.raises(DocumentConflict, match='ACCOUNT_ERASURE_PROJECT_CHANGED'):
        close(w, confirmation)
    assert not w.identities.events
    assert w.docs.get(('users', w.uid)).data['status'] == 'erasure_pending'
    assert w.docs.get(w.grant_path).data['bound_uid'] == EXTERNAL
    assert w.docs.get(root).data['status'] == changes['status']
