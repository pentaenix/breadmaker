"""Exact-memory canonical erasure proof; never a live purge implementation."""
import time
import pytest
from hosted.firestore.contracts import DocumentConflict
from hosted.security.saved_word_guard import LEDGER_COLLECTION
from hosted.security.saved_word_retention import POLICY_PATH
from hosted.runtime_services import _DEV_USER_ID as UID
from tests.test_v10376 import _world, _step, _content_snapshot
from tests.test_v10596_saved_word_retention import policy


def executor(world):
    from hosted.purge_planning import HostedPurgePlanner
    from hosted.purge_discovery import MemoryProjectTreeDiscovery
    from hosted.purge_receipts import HostedPurgePreparationService
    from hosted.purge_sandbox import HostedSandboxPurgeExecutor
    runtime, docs, _, recovery, _, _, clock = world
    planner = HostedPurgePlanner(runtime.projects, recovery,
        discovery=MemoryProjectTreeDiscovery(docs), now=lambda: clock[0],
        saved_word_retention_enabled=True, saved_word_memory_purge_enabled=True)
    return HostedSandboxPurgeExecutor(HostedPurgePreparationService(planner), now=lambda: clock[0])


def arm(world, *, ledger=True):
    _, docs, _, _, _, pid, _ = world
    if ledger:
        docs.run_transaction(lambda tx: tx.set((LEDGER_COLLECTION, pid), {'state': 'unknown', 'words': None}))
    run = executor(world)
    result = run.arm(pid, UID, operation_key='owner-consent')
    return run, result


def test_canonical_tombstone_invalidates_below_action_journal(monkeypatch):
    from hosted.action_journal import ActionDocumentStore
    from tests.test_v10596_saved_word_guard import setup
    from tests.test_v10364 import _replace
    app, raw, guard, pid, cid = setup(monkeypatch)
    assert type(app.projects.domain.documents) is ActionDocumentStore
    assert app.projects.domain.documents.store is guard
    app.projects.domain.saved_word_projection_enabled = True
    _replace(app, pid, cid, [{'text_content': 'two words'}], 'op_retention_compose')
    guard.initialize_metadata(pid, expected_revision=app.projects.get(pid)['generation'],
                              deadline=time.monotonic()+20)
    assert raw.get((LEDGER_COLLECTION, pid)).data['words'] == 2
    app.projects.delete(pid)
    assert raw.get(('projects', pid)).data['status'] == 'deleted'
    assert raw.get((LEDGER_COLLECTION, pid)).data['state'] == 'unknown'


def test_real_sandbox_purge_cleans_ledger_preserves_archive_and_replays(monkeypatch):
    world = _world(monkeypatch)
    _, docs, content, recovery, archive, pid, _ = world
    run, result = arm(world)
    receipt = docs.get(('purge_sandbox_executions', pid)).data
    assert len(receipt['saved_word_sidecars']) == 2
    assert all('data' not in row for row in receipt['saved_word_sidecars'])
    while result.state != 'sandbox_completed':
        result = _step(run, pid)
    assert docs.get(('projects', pid)) is None
    assert docs.get(('projects', pid, 'members', UID)) is None
    assert docs.get((LEDGER_COLLECTION, pid)) is None
    assert _content_snapshot(content, pid) == ()
    assert recovery.stat(archive.object_name) is not None
    assert _step(executor(world), pid) == result
    assert result.physical_purge_authorized is False


@pytest.mark.parametrize('changed', ['ledger', 'policy'])
def test_late_sidecar_insertion_blocks_before_any_object_delete(monkeypatch, changed):
    world = _world(monkeypatch)
    _, docs, content, _, _, pid, _ = world
    run, _ = arm(world, ledger=False)
    before = _content_snapshot(content, pid)
    target = (LEDGER_COLLECTION, pid) if changed == 'ledger' else POLICY_PATH
    value = {'words': 7} if changed == 'ledger' else policy()
    docs.run_transaction(lambda tx: tx.set(target, value))
    with pytest.raises(DocumentConflict, match='SIDECAR_DRIFT'):
        _step(run, pid)
    assert _content_snapshot(content, pid) == before
    assert docs.get(('projects', pid)) is not None


def test_final_root_ledger_receipt_transaction_rolls_back_together(monkeypatch):
    from hosted.firestore.memory import _MemoryTransaction
    world = _world(monkeypatch)
    _, docs, _, _, _, pid, _ = world
    run, result = arm(world)
    while result.cursor < result.total-1:
        result = _step(run, pid)
    root = docs.get(('projects', pid)); ledger = docs.get((LEDGER_COLLECTION, pid))
    receipt = docs.get(('purge_sandbox_executions', pid))
    original = _MemoryTransaction.delete
    def fail_ledger(tx, path):
        if tuple(path) == (LEDGER_COLLECTION, pid):
            raise RuntimeError('synthetic ledger failure after root staging')
        return original(tx, path)
    monkeypatch.setattr(_MemoryTransaction, 'delete', fail_ledger)
    with pytest.raises(RuntimeError):
        _step(run, pid)
    assert docs.get(('projects', pid)) == root
    assert docs.get((LEDGER_COLLECTION, pid)) == ledger
    assert docs.get(('projects', pid, 'members', UID)) is not None
    assert docs.get(('purge_sandbox_executions', pid)) == receipt


def test_policy_reference_still_blocks_opt_in_purge_preparation(monkeypatch):
    world = _world(monkeypatch)
    _, docs, content, _, _, pid, _ = world
    docs.run_transaction(lambda tx: tx.set(POLICY_PATH, policy()))
    before = _content_snapshot(content, pid)
    with pytest.raises(DocumentConflict, match='DISCOVERY_INCOMPLETE'):
        arm(world)
    assert docs.get(POLICY_PATH).data == policy()
    assert _content_snapshot(content, pid) == before


def test_guarded_purge_cannot_be_enabled_without_retention_census(monkeypatch):
    from hosted.purge_planning import HostedPurgePlanner
    from hosted.purge_discovery import MemoryProjectTreeDiscovery
    world = _world(monkeypatch)
    runtime, docs, _, recovery, _, _, _ = world
    with pytest.raises(ValueError, match='MEMORY_ONLY'):
        HostedPurgePlanner(runtime.projects, recovery, discovery=MemoryProjectTreeDiscovery(docs),
                           saved_word_memory_purge_enabled=True)
