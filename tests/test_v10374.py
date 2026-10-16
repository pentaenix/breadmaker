"""M9C2A source-only conformance: user runs the verifier, never the agent."""
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
from hosted.recovery_archives import HostedRecoveryArchiveService
from runtime.hosted import build_hosted_dev_runtime
from hosted.storage.memory import MemoryObjectStore
from hosted.runtime_services import _DEV_USER_ID


def _world(monkeypatch):
    monkeypatch.setenv('BREADMAKER_AUTH_MODE', 'developer')
    monkeypatch.setenv('BREADMAKER_ENV', 'development')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED', '1')
    docs, content, recovery = MemoryDocumentStore(), MemoryObjectStore(), MemoryObjectStore()
    runtime = build_hosted_dev_runtime(documents=docs, objects=content)
    pid = runtime.projects.create('Tombstone project')['id']
    return runtime, docs, content, recovery, pid


def _tombstone(runtime, docs, pid):
    runtime.projects.delete(pid)
    row = docs.get(('projects', pid))
    return datetime.fromisoformat(row.data['deleted_at'].replace('Z', '+00:00'))


def _planner(runtime, docs, recovery, stamp):
    return HostedPurgePlanner(
        runtime.projects, recovery,
        discovery=MemoryProjectTreeDiscovery(docs),
        now=lambda: stamp + timedelta(days=31),
    )


def test_v10374_discovery_finds_orphaned_and_unregistered_subcollections(monkeypatch):
    runtime, docs, content, recovery, pid = _world(monkeypatch)
    orphan = ('projects', pid, 'new_domain', 'missing-parent', 'nested', 'child')
    docs.run_transaction(lambda tx: tx.create(orphan, {'content': 'orphaned'}))
    stamp = _tombstone(runtime, docs, pid)
    plan = _planner(runtime, docs, recovery, stamp).preview(pid, _DEV_USER_ID)
    assert orphan in plan.firestore_paths
    assert ('projects', pid, 'new_domain', 'missing-parent') in plan.missing_parent_paths
    assert plan.document_inventory_exhaustive
    assert plan.public()['orphaned_parent_references'] == 1
    assert not plan.physical_purge_authorized
    assert ('projects', pid) in plan.firestore_paths


def test_v10374_no_discovery_stays_incomplete_and_never_prepares(monkeypatch):
    runtime, docs, content, recovery, pid = _world(monkeypatch)
    stamp = _tombstone(runtime, docs, pid)
    old = HostedPurgePlanner(runtime.projects, recovery, now=lambda: stamp + timedelta(days=31))
    assert not old.preview(pid, _DEV_USER_ID).document_inventory_exhaustive
    with pytest.raises(ValueError, match='HOSTED_PURGE_EXHAUSTIVE_DISCOVERY_REQUIRED'):
        HostedPurgePreparationService(old)


def test_v10374_preparation_requires_verified_predelete_independent_archive(monkeypatch):
    runtime, docs, content, recovery, pid = _world(monkeypatch)
    stamp = _tombstone(runtime, docs, pid)
    service = HostedPurgePreparationService(_planner(runtime, docs, recovery, stamp))
    with pytest.raises(DocumentConflict, match='HOSTED_PURGE_VERIFIED_INDEPENDENT_BACKUP_REQUIRED'):
        service.prepare(pid, _DEV_USER_ID, operation_key='first')
    assert docs.get(('purge_preparations', pid)) is None
    assert docs.get(('projects', pid)) is not None


def test_v10374_preparation_replays_across_fresh_instances_but_never_deletes(monkeypatch):
    runtime, docs, content, recovery, pid = _world(monkeypatch)
    archive = HostedRecoveryArchiveService(runtime.projects, recovery).create(
        pid, _DEV_USER_ID, operation='before_delete',
    )
    stamp = _tombstone(runtime, docs, pid)
    first = HostedPurgePreparationService(_planner(runtime, docs, recovery, stamp)).prepare(
        pid, _DEV_USER_ID, operation_key='user-confirmed-action',
    )
    second = HostedPurgePreparationService(_planner(runtime, docs, recovery, stamp)).resume_preview(
        pid, _DEV_USER_ID, operation_key='user-confirmed-action',
    )
    assert first == second
    assert first.archive_sha256 == archive.sha256
    assert not first.physical_purge_authorized and first.resume_cursor == 0
    assert first.public()['physical_purge_authorized'] is False
    assert docs.get(('projects', pid)) is not None
    assert recovery.stat(archive.object_name) is not None
    assert docs.get(('purge_preparations', pid)).data['state'] == 'prepared_read_only'
    with pytest.raises(DocumentConflict, match='HOSTED_PURGE_RECEIPT_CONFLICT'):
        HostedPurgePreparationService(_planner(runtime, docs, recovery, stamp)).prepare(
            pid, _DEV_USER_ID, operation_key='different-key',
        )


def test_v10374_inventory_drift_blocks_replayed_receipt(monkeypatch):
    runtime, docs, content, recovery, pid = _world(monkeypatch)
    HostedRecoveryArchiveService(runtime.projects, recovery).create(pid, _DEV_USER_ID, operation='pre')
    stamp = _tombstone(runtime, docs, pid)
    service = HostedPurgePreparationService(_planner(runtime, docs, recovery, stamp))
    service.prepare(pid, _DEV_USER_ID, operation_key='key')
    content.write_chunks(
        f'projects/{pid}/exports/new.breadmaker', [b'changed'], max_bytes=100,
        content_type='application/octet-stream', if_generation_match=0,
    )
    with pytest.raises(DocumentConflict, match='HOSTED_PURGE_RECEIPT_CONFLICT'):
        service.resume_preview(pid, _DEV_USER_ID, operation_key='key')
    assert docs.get(('projects', pid)) is not None


def test_v10374_wrong_owner_and_retention_block_preparation(monkeypatch):
    runtime, docs, content, recovery, pid = _world(monkeypatch)
    HostedRecoveryArchiveService(runtime.projects, recovery).create(pid, _DEV_USER_ID, operation='pre')
    stamp = _tombstone(runtime, docs, pid)
    too_early = HostedPurgePlanner(runtime.projects, recovery,
                                   discovery=MemoryProjectTreeDiscovery(docs),
                                   now=lambda: stamp + timedelta(days=29))
    with pytest.raises(DocumentConflict, match='HOSTED_PURGE_RETENTION_NOT_ELAPSED'):
        HostedPurgePreparationService(too_early).prepare(pid, _DEV_USER_ID, operation_key='test')
    with pytest.raises(ProjectUnavailable):
        HostedPurgePreparationService(_planner(runtime, docs, recovery, stamp)).prepare(
            pid, 'usr_' + 'f' * 32, operation_key='test',
        )
    assert docs.get(('purge_preparations', pid)) is None


def test_v10374_unbounded_or_failed_scan_refuses_to_authorize(monkeypatch):
    runtime, docs, content, recovery, pid = _world(monkeypatch)
    docs.run_transaction(lambda tx: tx.create(
        ('projects', pid, 'orphan', 'ghost', 'leaves', 'first'), {'a': 1},
    ))
    stamp = _tombstone(runtime, docs, pid)
    with pytest.raises(ValueError, match='HOSTED_PURGE_INVENTORY_TOO_LARGE'):
        HostedPurgePlanner(runtime.projects, recovery, max_entries=1,
                           discovery=MemoryProjectTreeDiscovery(docs),
                           now=lambda: stamp + timedelta(days=31)).preview(pid, _DEV_USER_ID)
    assert docs.get(('projects', pid)) is not None


def test_v10374_google_administrative_rpc_requires_show_missing_and_walks_orphans():
    """Credential-free administrative API contract; no GCP client imported."""
    from types import SimpleNamespace
    from hosted.purge_discovery import GoogleProjectTreeDiscovery

    pid = 'a' * 32
    root = f'projects/{pid}'

    class Ref:
        def __init__(self, client, path):
            self.client, self.path = client, path
            self._document_path = 'projects/mock/databases/(default)/documents/' + path

        def get(self):
            return SimpleNamespace(exists=True, update_time='root-42')

        def collections(self):
            names = self.client.collections_map.get(self.path, ())
            return [SimpleNamespace(id=name, path=self.path + '/' + name) for name in names]

    class API:
        def __init__(self):
            self.calls = []

        def list_documents(self, *, request):
            self.calls.append(request)
            assert request['show_missing'] is True
            parent = request['parent'].split('/documents/', 1)[1]
            for path, exists in rows.get((parent, request['collection_id']), ()):
                yield SimpleNamespace(
                    name='projects/mock/databases/(default)/documents/' + path,
                    create_time=(object() if exists else None), update_time=None,
                )

    class Client:
        def __init__(self):
            self._firestore_api = API()
            self.collections_map = {
                root: ('orphan_parent',),
                root + '/orphan_parent/missing': ('deep',),
            }

        def document(self, path):
            return Ref(self, path)

    rows = {
        (root, 'orphan_parent'): ((root + '/orphan_parent/missing', False),),
        (root + '/orphan_parent/missing', 'deep'): (
            (root + '/orphan_parent/missing/deep/actual', True),
        ),
    }
    client = Client()
    tree = GoogleProjectTreeDiscovery(SimpleNamespace(_client=client)).scan(pid, max_entries=30)
    assert ('projects', pid, 'orphan_parent', 'missing') in tree.missing_parents
    assert ('projects', pid, 'orphan_parent', 'missing', 'deep', 'actual') in tree.documents
    assert tree.complete
    assert len(client._firestore_api.calls) == 2


def test_v10374_google_discovery_refuses_missing_administrative_rpc():
    from types import SimpleNamespace
    from hosted.purge_discovery import GoogleProjectTreeDiscovery
    with pytest.raises(ValueError, match='HOSTED_PURGE_DISCOVERY_SHOW_MISSING_REQUIRED'):
        GoogleProjectTreeDiscovery(SimpleNamespace(_client=object()))


def test_v10374_nested_orphan_revision_change_invalidates_receipt(monkeypatch):
    runtime, docs, content, recovery, pid = _world(monkeypatch)
    orphan = ('projects', pid, 'future', 'missing', 'deep', 'child')
    docs.run_transaction(lambda tx: tx.create(orphan, {'value': 1}))
    HostedRecoveryArchiveService(runtime.projects, recovery).create(pid, _DEV_USER_ID, operation='pre')
    stamp = _tombstone(runtime, docs, pid)
    service = HostedPurgePreparationService(_planner(runtime, docs, recovery, stamp))
    service.prepare(pid, _DEV_USER_ID, operation_key='key')
    # Deliberately does NOT touch the project root generation.
    docs.run_transaction(lambda tx: tx.set(orphan, {'value': 2}))
    with pytest.raises(DocumentConflict, match='HOSTED_PURGE_RECEIPT_CONFLICT'):
        service.resume_preview(pid, _DEV_USER_ID, operation_key='key')
