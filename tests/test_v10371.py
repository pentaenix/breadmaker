"""M9C1 read-only deletion inventory. USER RUNS THE VERIFIER, NOT THE AGENT."""
from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hosted.firestore.contracts import DocumentConflict, ProjectUnavailable
from hosted.firestore.memory import MemoryDocumentStore
from hosted.purge_planning import HostedPurgePlanner
from hosted.runtime_services import _DEV_USER_ID
from hosted.storage.memory import MemoryObjectStore
from runtime.hosted import build_hosted_dev_runtime


def _world(monkeypatch):
    monkeypatch.setenv('BREADMAKER_AUTH_MODE', 'developer')
    monkeypatch.setenv('BREADMAKER_ENV', 'development')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED', '1')
    docs, content, recovery = MemoryDocumentStore(), MemoryObjectStore(), MemoryObjectStore()
    runtime = build_hosted_dev_runtime(documents=docs, objects=content)
    pid = runtime.projects.create('My manuscript')['id']
    return runtime, docs, content, recovery, pid


def _write(store, name, data=b'payload'):
    return store.write_chunks(name, [data], max_bytes=1000, content_type='application/octet-stream', if_generation_match=0)


def _deleted(runtime, documents, pid):
    runtime.projects.delete(pid)
    row = documents.get(('projects', pid))
    return datetime.fromisoformat(row.data['deleted_at'].replace('Z', '+00:00'))


def test_v10371_owner_only_tombstone_required_for_inventory(monkeypatch):
    runtime, docs, content, recovery, pid = _world(monkeypatch)
    planner = HostedPurgePlanner(runtime.projects, recovery)
    with pytest.raises(ValueError, match='HOSTED_PURGE_TOMBSTONE_REQUIRED'):
        planner.preview(pid, _DEV_USER_ID)
    _deleted(runtime, docs, pid)
    with pytest.raises(ProjectUnavailable):
        planner.preview(pid, 'usr_' + 'f' * 32)
    result = planner.preview(pid, _DEV_USER_ID)
    assert result.project_id == pid and result.physical_purge_authorized is False
    assert result.public()['physical_purge_authorized'] is False


def test_v10371_preview_scopes_deep_docs_and_objects_without_erasure(monkeypatch):
    runtime, docs, content, recovery, pid = _world(monkeypatch)
    other = runtime.projects.create('Other manuscript')['id']
    canvas = runtime.projects.domain.create('canvas', {'project_id': pid}, {'name': 'Timeline'}, user_id=_DEV_USER_ID)
    node = runtime.projects.domain.create('canvas_node', {'project_id': pid, 'canvas_id': canvas['id']},
                                          {'name': 'Anchor', 'x': 12, 'y': 24}, user_id=_DEV_USER_ID)
    own = _write(content, f'projects/{pid}/exports/canonical/export.breadmaker')
    alien = _write(content, f'projects/{other}/exports/canonical/export.breadmaker')
    user_asset = _write(content, f'users/{_DEV_USER_ID}/avatars/one.webp')
    backup = _write(recovery, f'recovery/v1/projects/{pid}/private/snapshot.breadmaker')
    _deleted(runtime, docs, pid)
    plan = HostedPurgePlanner(runtime.projects, recovery).preview(pid, _DEV_USER_ID)
    assert ('projects', pid, 'canvases', canvas['id'], 'nodes', node['id']) in plan.firestore_paths
    assert (own.name, own.generation, own.sha256) in plan.content_objects
    assert any('/block-payloads/' in name for name, _, _ in plan.content_objects)
    assert all(name.startswith(f'projects/{pid}/') for name, _, _ in plan.content_objects)
    assert alien.name not in {name for name, _, _ in plan.content_objects}
    assert user_asset.name not in {name for name, _, _ in plan.content_objects}
    assert plan.retained_recovery_objects == ((backup.name, backup.generation, backup.sha256),)
    assert content.stat(alien.name) and content.stat(user_asset.name) and content.stat(own.name)
    assert recovery.stat(backup.name) is not None
    assert plan.public()['recovery_archives_preserved'] == 1
    assert not plan.document_inventory_exhaustive


def test_v10371_retention_floor_and_time_bound_cannot_authorize_purge(monkeypatch):
    runtime, docs, content, recovery, pid = _world(monkeypatch)
    stamp = _deleted(runtime, docs, pid)
    with pytest.raises(ValueError, match='HOSTED_PURGE_RETENTION_FLOOR_REQUIRED'):
        HostedPurgePlanner(runtime.projects, recovery, retention_days=1)
    before = HostedPurgePlanner(runtime.projects, recovery, now=lambda: stamp + timedelta(days=29)).preview(pid, _DEV_USER_ID)
    after = HostedPurgePlanner(runtime.projects, recovery, now=lambda: stamp + timedelta(days=31)).preview(pid, _DEV_USER_ID)
    assert not before.retention_elapsed and after.retention_elapsed
    assert not before.physical_purge_authorized and not after.physical_purge_authorized
    assert after.eligible_after == (stamp + timedelta(days=30)).isoformat().replace('+00:00', 'Z')


def test_v10371_inventory_aborts_on_concurrent_project_change(monkeypatch):
    runtime, docs, content, recovery, pid = _world(monkeypatch)
    _deleted(runtime, docs, pid)
    original = content.list_prefix
    def race(prefix):
        snapshot = original(prefix)
        project = docs.get(('projects', pid))
        docs.run_transaction(lambda tx: tx.set(('projects', pid), dict(project.data, deletion_note='changed')))
        return snapshot
    monkeypatch.setattr(content, 'list_prefix', race)
    with pytest.raises(DocumentConflict, match='HOSTED_PURGE_CONCURRENT_MUTATION'):
        HostedPurgePlanner(runtime.projects, recovery).preview(pid, _DEV_USER_ID)


def test_v10371_budget_and_bucket_separation_are_fail_closed(monkeypatch):
    runtime, docs, content, recovery, pid = _world(monkeypatch)
    _deleted(runtime, docs, pid)
    with pytest.raises(ValueError, match='HOSTED_PURGE_INDEPENDENT_RECOVERY_REQUIRED'):
        HostedPurgePlanner(runtime.projects, content)
    with pytest.raises(ValueError, match='HOSTED_PURGE_INVENTORY_TOO_LARGE'):
        HostedPurgePlanner(runtime.projects, recovery, max_entries=1).preview(pid, _DEV_USER_ID)


def test_v10371_fingerprint_stable_for_identical_plans_and_changes_with_content(monkeypatch):
    runtime, docs, content, recovery, pid = _world(monkeypatch)
    _deleted(runtime, docs, pid)
    planner = HostedPurgePlanner(runtime.projects, recovery)
    first = planner.preview(pid, _DEV_USER_ID)
    second = planner.preview(pid, _DEV_USER_ID)
    assert first.fingerprint == second.fingerprint
    _write(content, f'projects/{pid}/exports/next/export.json')
    third = planner.preview(pid, _DEV_USER_ID)
    assert third.fingerprint != first.fingerprint
    assert len(third.fingerprint) == 64 and third.physical_purge_authorized is False
