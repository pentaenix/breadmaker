"""M9C2B: credential-free purge sandbox conformance; operator runs verifier."""
from __future__ import annotations

from datetime import datetime, timedelta
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hosted.firestore.contracts import DocumentConflict, ProjectUnavailable
from hosted.firestore.memory import MemoryDocumentStore
from hosted.purge_discovery import MemoryProjectTreeDiscovery
from hosted.purge_planning import HostedPurgePlanner
from hosted.purge_receipts import HostedPurgePreparationService
from hosted.purge_sandbox import HostedSandboxPurgeExecutor, SimulatedPurgeCrash
from hosted.recovery_archives import HostedRecoveryArchiveService
from hosted.runtime_services import _DEV_USER_ID
from hosted.storage.memory import MemoryObjectStore
from runtime.hosted import build_hosted_dev_runtime


def _world(monkeypatch, *, objects=1, with_orphan=False):
    monkeypatch.setenv('BREADMAKER_AUTH_MODE', 'developer')
    monkeypatch.setenv('BREADMAKER_ENV', 'development')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED', '1')
    docs, content, recovery = MemoryDocumentStore(), MemoryObjectStore(), MemoryObjectStore()
    runtime = build_hosted_dev_runtime(documents=docs, objects=content)
    pid = runtime.projects.create('Sandbox delete test')['id']
    for i in range(objects):
        content.write_chunks(f'projects/{pid}/exports/owned_{i}.breadmaker', [b'own'],
                             max_bytes=16, content_type='application/octet-stream',
                             if_generation_match=0)
    if with_orphan:
        docs.run_transaction(lambda tx: tx.create(
            ('projects', pid, 'future', 'missing', 'nested', 'orphan'), {'text': 'do not lose'},
        ))
    archive = HostedRecoveryArchiveService(runtime.projects, recovery).create(
        pid, _DEV_USER_ID, operation='before_purge',
    )
    runtime.projects.delete(pid)
    root = docs.get(('projects', pid))
    stamp = datetime.fromisoformat(root.data['deleted_at'].replace('Z', '+00:00'))
    clock = [stamp + timedelta(days=31)]
    return runtime, docs, content, recovery, archive, pid, clock


def _executor(runtime, docs, recovery, clock):
    planner = HostedPurgePlanner(
        runtime.projects, recovery, discovery=MemoryProjectTreeDiscovery(docs),
        now=lambda: clock[0],
    )
    return HostedSandboxPurgeExecutor(HostedPurgePreparationService(planner), now=lambda: clock[0])


def _content_snapshot(content, pid):
    """Exact scoped GCS inventory, including generated immutable block payloads.

    A project's initial manuscript can create objects before the test adds any
    explicit export objects. Comparing just object counts is not a meaningful
    no-deletion assertion.
    """
    return tuple(sorted(
        (item.name, item.generation, item.sha256)
        for item in content.list_prefix(f'projects/{pid}/')
    ))


def _armed(world):
    runtime, docs, content, recovery, archive, pid, clock = world
    executor = _executor(runtime, docs, recovery, clock)
    start = executor.arm(pid, _DEV_USER_ID, operation_key='owner-consent')
    return executor, start


def _step(executor, pid, *, worker='worker_one', **kwargs):
    return executor.step(pid, _DEV_USER_ID, operation_key='owner-consent',
                         worker_id=worker, **kwargs)


def test_v10376_arm_is_durable_non_destructive_and_still_not_production_authority(monkeypatch):
    world = _world(monkeypatch, objects=2, with_orphan=True)
    runtime, docs, content, recovery, archive, pid, clock = world
    before = _content_snapshot(content, pid)
    assert any('/block-payloads/' in name for name, _, _ in before)
    executor, start = _armed(world)
    assert start.state == 'sandbox_armed' and start.cursor == 0 and start.total >= 3
    assert not start.physical_purge_authorized
    assert docs.get(('projects', pid)) is not None
    assert _content_snapshot(content, pid) == before
    assert recovery.stat(archive.object_name) is not None
    assert docs.get(('purge_preparations', pid)).data['state'] == 'prepared_read_only'
    assert executor.arm(pid, _DEV_USER_ID, operation_key='owner-consent') == start
    assert _executor(runtime, docs, recovery, clock).arm(
        pid, _DEV_USER_ID, operation_key='owner-consent') == start
    with pytest.raises(DocumentConflict, match='RECEIPT_CONFLICT'):
        executor.arm(pid, _DEV_USER_ID, operation_key='different-key')


def test_v10376_bounded_steps_complete_leaf_first_and_preserve_other_scopes(monkeypatch):
    world = _world(monkeypatch, objects=2, with_orphan=True)
    runtime, docs, content, recovery, archive, pid, clock = world
    # These are NEVER project deletion candidates.
    other_doc = ('users', _DEV_USER_ID, 'settings', 'preferences')
    docs.run_transaction(lambda tx: tx.create(other_doc, {'language': 'en'}))
    external = content.write_chunks('imports/' + _DEV_USER_ID + '/upload/file.zip', [b'other'],
                                   max_bytes=16, content_type='application/zip', if_generation_match=0)
    executor, start = _armed(world)
    current = start
    for i in range(start.total):
        fresh = _executor(runtime, docs, recovery, clock)  # separate runtime worker
        next_step = _step(fresh, pid, worker='worker_one')
        assert next_step.cursor == current.cursor + 1
        current = next_step
        assert recovery.stat(archive.object_name) is not None
    assert current.state == 'sandbox_completed' and current.cursor == start.total
    assert docs.get(('projects', pid)) is None
    assert docs.get(other_doc) is not None
    assert not content.list_prefix(f'projects/{pid}/')
    assert content.stat(external.name) is not None
    assert _step(_executor(runtime, docs, recovery, clock), pid) == current
    # The original read-only approval remains; it was not a delete command.
    assert docs.get(('purge_preparations', pid)).data['resume_cursor'] == 0


def test_v10376_object_crash_after_delete_recovers_only_preclaimed_target(monkeypatch):
    world = _world(monkeypatch, objects=1)
    runtime, docs, content, recovery, archive, pid, clock = world
    executor, start = _armed(world)
    target = next(iter(content.list_prefix(f'projects/{pid}/')))
    with pytest.raises(SimulatedPurgeCrash):
        _step(executor, pid, crash_after_object_delete=True)
    pending = docs.get(('purge_sandbox_executions', pid)).data
    assert pending['inflight'] == 0 and pending['cursor'] == 0
    assert content.stat(target.name) is None
    with pytest.raises(DocumentConflict, match='LEASE_HELD'):
        _step(_executor(runtime, docs, recovery, clock), pid, worker='worker_two')
    clock[0] += timedelta(seconds=31)
    recovered = _step(_executor(runtime, docs, recovery, clock), pid, worker='worker_two')
    assert recovered.cursor == 1
    assert docs.get(('projects', pid)) is not None
    assert recovery.stat(archive.object_name) is not None


def test_v10376_changed_generation_after_claim_refuses_replay(monkeypatch):
    world = _world(monkeypatch, objects=1)
    runtime, docs, content, recovery, archive, pid, clock = world
    executor, start = _armed(world)
    target = content.list_prefix(f'projects/{pid}/')[0]
    with pytest.raises(SimulatedPurgeCrash):
        _step(executor, pid, crash_after_object_delete=True)
    replacement = content.write_chunks(target.name, [b'replacement'], max_bytes=32,
                                       content_type='application/octet-stream', if_generation_match=0)
    clock[0] += timedelta(seconds=31)
    with pytest.raises(DocumentConflict, match='OBJECT_DRIFT'):
        _step(_executor(runtime, docs, recovery, clock), pid, worker='worker_two')
    assert content.stat(target.name).generation == replacement.generation
    assert docs.get(('purge_sandbox_executions', pid)).data['cursor'] == 0


def test_v10376_new_orphan_or_revision_change_aborts_before_any_delete(monkeypatch):
    world = _world(monkeypatch, objects=1)
    runtime, docs, content, recovery, archive, pid, clock = world
    executor, start = _armed(world)
    alien = ('projects', pid, 'surprise', 'missing', 'deep', 'child')
    docs.run_transaction(lambda tx: tx.create(alien, {'new': 1}))
    before = _content_snapshot(content, pid)
    with pytest.raises(DocumentConflict, match='DOCUMENT_DRIFT'):
        _step(executor, pid)
    assert _content_snapshot(content, pid) == before
    assert docs.get(alien) is not None


def test_v10376_backup_tamper_blocks_execution(monkeypatch):
    world = _world(monkeypatch, objects=1)
    runtime, docs, content, recovery, archive, pid, clock = world
    executor, start = _armed(world)
    meta = recovery.stat(archive.object_name)
    recovery.delete(archive.object_name, if_generation_match=meta.generation)
    before = _content_snapshot(content, pid)
    with pytest.raises(DocumentConflict, match='BACKUP_CHANGED'):
        _step(executor, pid)
    assert _content_snapshot(content, pid) == before


def test_v10376_foreign_caller_and_non_memory_provider_do_not_get_executor(monkeypatch):
    world = _world(monkeypatch, objects=1)
    runtime, docs, content, recovery, archive, pid, clock = world
    executor, start = _armed(world)
    with pytest.raises(DocumentConflict, match='RECEIPT_CONFLICT'):
        executor.step(pid, 'usr_' + 'f'*32, operation_key='owner-consent', worker_id='worker')
    from types import SimpleNamespace
    with pytest.raises(ValueError, match='MEMORY_ONLY'):
        HostedSandboxPurgeExecutor(SimpleNamespace(planner=SimpleNamespace(
            documents=docs, content_objects=content, recovery_objects=recovery,
            discovery=object(),
        )))
    assert docs.get(('projects', pid)) is not None


def test_v10376_manifest_budget_rejects_unbounded_work(monkeypatch):
    world = _world(monkeypatch, objects=129)
    runtime, docs, content, recovery, archive, pid, clock = world
    executor = _executor(runtime, docs, recovery, clock)
    with pytest.raises(ValueError, match='MANIFEST_TOO_LARGE'):
        executor.arm(pid, _DEV_USER_ID, operation_key='owner-consent')
    assert docs.get(('purge_sandbox_executions', pid)) is None
    assert docs.get(('projects', pid)) is not None


def test_v10376_retention_is_rechecked_after_arming(monkeypatch):
    world = _world(monkeypatch, objects=1)
    runtime, docs, content, recovery, archive, pid, clock = world
    executor, start = _armed(world)
    clock[0] -= timedelta(days=3)
    before = _content_snapshot(content, pid)
    with pytest.raises(DocumentConflict, match='RETENTION_NOT_ELAPSED'):
        _step(executor, pid)
    assert docs.get(('purge_sandbox_executions', pid)).data['cursor'] == 0
    assert _content_snapshot(content, pid) == before


def test_v10376_firestore_transaction_retry_does_not_double_advance(monkeypatch):
    world = _world(monkeypatch, objects=0)
    runtime, docs, content, recovery, archive, pid, clock = world
    executor, start = _armed(world)
    docs.retry_next_transaction()
    progressed = _step(executor, pid)
    assert progressed.cursor == 1
    assert docs.get(('purge_sandbox_executions', pid)).data['cursor'] == 1
    assert recovery.stat(archive.object_name) is not None
