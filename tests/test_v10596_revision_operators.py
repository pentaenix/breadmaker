"""Physical operator inventories and normalized active-only revision packages."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from types import SimpleNamespace
from zipfile import ZipFile, ZIP_DEFLATED

import pytest

from hosted.action_journal import ActionDocumentStore
from hosted.account_lifecycle import AccountLifecycleDocumentStore
from hosted.firestore.contracts import DocumentConflict, HostedValidationError
from hosted.firestore.repositories import HostedProjectRepository, HostedStructuredRepository, HostedUserRepository
from hosted.manuscript_visibility import FORMAT_FIELD, HEAD_FIELD, REVISION_FIELD, ManuscriptDocumentStore
from hosted.operator_recovery_plan import HostedRecoveryJobPlanner
from hosted.packages import HostedProjectPackageService
from hosted.purge_discovery import MemoryProjectTreeDiscovery
from hosted.purge_planning import HostedPurgePlanner
from hosted.purge_receipts import HostedPurgePreparationService
from hosted.purge_sandbox import HostedSandboxPurgeExecutor
from hosted.recovery_archives import HostedRecoveryArchiveService
from hosted.recovery_coverage import RecoveryCoverageError, physical_project_fingerprint
from hosted.runtime_services import HostedCloudProjectRepository
from hosted.storage.memory import MemoryObjectStore
from tests.test_v10382 import _records, UID, NOW
from tests.test_v10594 import actor
from tests.test_v10596_revision_staging import setup, parsed, stage, publish, texts
from tests.test_v10353 import OWNER


def projects_for(documents, objects):
    return HostedCloudProjectRepository(documents, objects, HostedProjectRepository(documents),
        HostedUserRepository(HostedStructuredRepository(documents)), lambda: OWNER)


def rewrite_root(raw, pid, **changes):
    path = ('projects', pid)
    raw.run_transaction(lambda tx: tx.set(path, {**tx.get(path).data, **changes}))


def test_raw_recovery_plan_accepts_encoded_active_without_changing_physical_root():
    raw = _records()
    pid = raw.query_collection(('projects',))[0].path[1]
    rewrite_root(raw, pid, status='active_manuscript_v1', **{FORMAT_FIELD: 1})
    before = deepcopy(raw._documents)
    planner = HostedRecoveryJobPlanner(raw)
    plan = planner.inspect(now=NOW)
    assert [item.project_id for item in plan.due_items] == [pid]
    assert not plan.execution_authorized
    assert planner.revalidate(plan, now=NOW) == plan
    assert raw._documents == before
    rewrite_root(raw, pid, status='deleted_manuscript_v1')
    with pytest.raises(DocumentConflict, match='OWNER_OR_PROJECT_CHANGED'):
        planner.revalidate(plan, now=NOW)


@pytest.mark.parametrize('changes', [
    {'status': 'active_manuscript_v1'},
    {'status': 'active_manuscript_v1', FORMAT_FIELD: True},
    {'status': 'active_manuscript_v1', FORMAT_FIELD: 2},
    {'status': 'active', FORMAT_FIELD: 1},
    {'status': 'active_manuscript_v1', FORMAT_FIELD: 1, '_bm_erasure_operation': 'closure'},
])
def test_recovery_plan_rejects_unknown_formats_or_erasure(changes):
    raw = _records()
    pid = raw.query_collection(('projects',))[0].path[1]
    rewrite_root(raw, pid, **changes)
    with pytest.raises((HostedValidationError, DocumentConflict)):
        HostedRecoveryJobPlanner(raw).inspect(now=NOW)


def revision_world():
    make, raw, objects, pid = setup()
    with actor():
        job = stage(make, pid, parsed(3, 'Published'), 'portable-published', replace_main=True)
        publish(make, pid, job)
        staged = stage(make, pid, parsed(2, 'Unpublished'), 'portable-unpublished', replace_main=True, confirmed=True)
    return make, raw, objects, pid, staged


def test_portable_zip_json_and_local_export_contain_only_active_marker_free_rows():
    make, raw, objects, pid, _ = revision_world()
    with actor():
        projects = projects_for(make().domain.documents, make().domain.objects)
        package = HostedProjectPackageService(projects)
        before = deepcopy(raw._documents)
        encoded = package.export_bytes(pid, OWNER)
        as_json = package.export_json(pid, OWNER)
        portable = package.export_local_bytes(pid, OWNER)
        assert raw._documents == before
        with ZipFile(BytesIO(encoded)) as archive:
            snapshot = json.loads(archive.read('project.json'))
            assert all(not name.startswith('manuscript') for name in archive.namelist())
        rows = snapshot['entities']['manuscript_block']
        assert [row['data']['text_content'] for row in rows] == texts(make, pid)
        assert len(rows) == 3
        assert 'Unpublished' not in json.dumps(snapshot)
        assert '_bm_' not in json.dumps(snapshot) and '_bm_' not in json.dumps(as_json)
        assert snapshot['entities'] == as_json['entities']
        assert len(raw.query_collection(('projects', pid, 'blocks'))) > len(rows)
        for source in (encoded, portable):
            imported = package.import_bytes(source, OWNER, operation='normalized-import-' + str(len(source)))
            destination = imported['id']
            assert FORMAT_FIELD not in raw.get(('projects', destination)).data
            assert [row['text_content'] for row in projects.domain.list('manuscript_block',
                {'project_id': destination}, user_id=OWNER)] == texts(make, pid)
            for family in ('tabs', 'chapters', 'blocks', 'scenes', 'assets', 'annotations'):
                assert all(not any(key.startswith('_bm_manuscript_') for key in row.data)
                    for row in raw.query_collection(('projects', destination, family)))


@pytest.mark.parametrize('field', [FORMAT_FIELD, HEAD_FIELD, REVISION_FIELD])
def test_portable_import_rejects_forged_revision_metadata(field):
    make, raw, objects, pid, _ = revision_world()
    with actor():
        package = HostedProjectPackageService(projects_for(make().domain.documents, make().domain.objects))
        payload = package.export_json(pid, OWNER)
        payload['entities']['chapter'][0]['data'][field] = 'forged'
        # No new authority is published even if ordinary rows were staged first.
        before = len(raw.query_collection(('projects',)))
        with pytest.raises(HostedValidationError, match='RESERVED'):
            package.import_json(payload, OWNER)
        assert len(raw.query_collection(('projects',))) == before


def test_active_product_archive_is_not_a_full_retained_backup():
    make, raw, objects, pid, _ = revision_world()
    recovery = MemoryObjectStore()
    with actor():
        projects = projects_for(make().domain.documents, make().domain.objects)
        archive = HostedRecoveryArchiveService(projects, recovery).create(pid, OWNER, operation='product-state')
        payload = b''.join(recovery.read_chunks(archive.object_name, max_bytes=archive.size))
        with ZipFile(BytesIO(payload)) as bundle:
            data = bundle.read('project.json')
        assert b'Published' in data and b'Unpublished' not in data and b'_bm_manuscript_' not in data
        # The operator-only sealed path must see the physical format even when
        # normal readers hide its root marker and retained descendants.
        normal = make().domain.documents
        assert physical_project_fingerprint(normal, pid) == physical_project_fingerprint(raw, pid)
        with pytest.raises(RecoveryCoverageError, match='MANUSCRIPT_REVISIONS_NOT_READY'):
            physical_project_fingerprint(normal, pid, require_legacy_recovery=True)


def test_physical_trash_preview_includes_staged_retired_and_unknown_orphan_rows():
    make, raw, objects, pid, _ = revision_world()
    recovery = MemoryObjectStore()
    orphan = ('projects', pid, 'unknown', 'missing', 'children', 'retained')
    raw.run_transaction(lambda tx: tx.create(orphan, {'keep': True}))
    publication = ('projects', pid, 'asset_publications', 'asset_' + 'a' * 64)
    raw.run_transaction(lambda tx: tx.create(publication, {'format': 1, 'project_id': pid}))
    stamp = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rewrite_root(raw, pid, status='deleted_manuscript_v1', deleted_at=stamp.isoformat())
    physical_projects = projects_for(raw, objects)
    before = deepcopy(raw._documents)
    planner = HostedPurgePlanner(physical_projects, recovery,
        discovery=MemoryProjectTreeDiscovery(raw), now=lambda: stamp + timedelta(days=31))
    plan = planner.preview(pid, OWNER)
    assert plan.document_inventory_exhaustive and plan.retention_elapsed
    assert not plan.physical_purge_authorized
    assert set(plan.firestore_paths) == {path for path in raw._documents if path[:2] == ('projects', pid)}
    assert orphan in plan.firestore_paths and publication in plan.firestore_paths
    assert ('projects', pid, 'unknown', 'missing') in plan.missing_parent_paths
    assert raw._documents == before
    service = HostedPurgePreparationService(planner)
    with pytest.raises(DocumentConflict, match='RETAINED_REVISION_BACKUP_REQUIRED'):
        service.prepare(pid, OWNER, operation_key='never-purge-product-only-snapshot')
    assert raw.get(('purge_preparations', pid)) is None
    executor = HostedSandboxPurgeExecutor(service)
    with pytest.raises(DocumentConflict, match='TOMBSTONE_CHANGED'):
        executor._assert_tombstone(pid, OWNER, {'deleted_at': stamp.isoformat(), 'project_generation': plan.project_generation})
    assert raw._documents == before


def test_operator_physical_selection_unwraps_exact_views_but_keeps_lifecycle_fence():
    make, raw, objects, pid, _ = revision_world()
    documents = make().domain.documents
    projects = projects_for(documents, objects)
    recovery = MemoryObjectStore()
    fence = AccountLifecycleDocumentStore(raw)
    for source in (ManuscriptDocumentStore(raw), ActionDocumentStore(ManuscriptDocumentStore(raw))):
        assert HostedPurgePlanner(projects_for(source, objects), recovery).documents is raw
    protected = ActionDocumentStore(ManuscriptDocumentStore(fence))
    assert HostedPurgePlanner(projects_for(protected, objects), recovery).documents is fence
    # Never unwrap a fence to reach a view placed below it.
    with pytest.raises(ValueError, match='PHYSICAL_STORE_REQUIRED'):
        HostedPurgePlanner(projects_for(AccountLifecycleDocumentStore(ManuscriptDocumentStore(raw)), objects), recovery)
    stamp = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rewrite_root(raw, pid, status='deleted_manuscript_v1', deleted_at=stamp.isoformat())
    planner = HostedPurgePlanner(projects, recovery, physical_documents=raw,
        discovery=MemoryProjectTreeDiscovery(raw), now=lambda: stamp + timedelta(days=31))
    assert planner.documents is raw
    assert len(planner.preview(pid, OWNER).firestore_paths) == len(raw.project_document_inventory(pid))


@pytest.mark.parametrize('changes', [
    {FORMAT_FIELD: 2}, {FORMAT_FIELD: None}, {'status': 'deleted'},
    {'status': 'erasure_pending', '_bm_erasure_operation': 'closure'},
])
def test_physical_trash_preview_rejects_invalid_format_or_erasure(changes):
    make, raw, objects, pid, _ = revision_world()
    rewrite_root(raw, pid, status='deleted_manuscript_v1', deleted_at='2026-10-01T00:00:00Z')
    rewrite_root(raw, pid, **changes)
    planner = HostedPurgePlanner(projects_for(raw, objects), MemoryObjectStore())
    with pytest.raises((HostedValidationError, ValueError)):
        planner.preview(pid, OWNER)


def test_format_adoption_during_backup_check_cannot_create_legacy_purge_receipt(monkeypatch):
    from tests.test_v10374 import _world, _tombstone, _planner
    runtime, raw, objects, recovery, pid = _world(monkeypatch)
    HostedRecoveryArchiveService(runtime.projects, recovery).create(pid, runtime.projects._uid(None), operation='before-delete')
    stamp = _tombstone(runtime, raw, pid)
    service = HostedPurgePreparationService(_planner(runtime, raw, recovery, stamp))
    original = service._verified_backup
    def adopt_after_check(*args):
        record = original(*args)
        rewrite_root(raw, pid, status='deleted_manuscript_v1', **{FORMAT_FIELD: 1})
        return record
    monkeypatch.setattr(service, '_verified_backup', adopt_after_check)
    with pytest.raises(DocumentConflict, match='CONCURRENT_MUTATION'):
        service.prepare(pid, runtime.projects._uid(None), operation_key='format-race')
    assert raw.get(('purge_preparations', pid)) is None


def test_read_only_archive_writer_composes_active_revision_view(monkeypatch):
    from hosted import recovery_writer_cli as cli
    make, raw, objects, pid, _ = revision_world()
    recovery = MemoryObjectStore()
    raw.run_transaction(lambda tx: tx.create(('recovery_schedules', pid), {
        'project_id': pid, 'owner_user_id': OWNER, 'enabled': True,
        'cadence_hours': 24, 'retention_days': 30, 'next_due_epoch': int(NOW.timestamp()) - 1,
    }))
    class ReadOnlyTransaction:
        def __init__(self, tx): self.tx = tx
        def get(self, path):
            assert path[:2] == ('projects', pid)
            return self.tx.get(path)
        def get_many(self, paths): return [self.get(path) for path in paths]
        def query_collection(self, path, **kwargs):
            assert path[:2] == ('projects', pid)
            return self.tx.query_collection(path, **kwargs)
        def query_collection_in(self, path, **kwargs):
            assert path[:2] == ('projects', pid)
            return self.tx.query_collection_in(path, **kwargs)
    class ReadOnlyStore:
        def get(self, path): return self.run_transaction(lambda tx: tx.get(path))
        def run_transaction(self, fn, **kwargs):
            return raw.run_transaction(lambda tx: fn(ReadOnlyTransaction(tx)))
    monkeypatch.setattr(cli, 'GoogleFirestoreDocumentStore', lambda **kwargs: raw)
    monkeypatch.setattr(cli, '_ProjectReadStore', lambda docs, project: ReadOnlyStore())
    monkeypatch.setattr(cli, 'GoogleCloudObjectStore', lambda bucket, **kwargs: objects if bucket == 'source' else recovery)
    monkeypatch.setattr(cli, '_LiveObjectReader', lambda source: source)
    monkeypatch.setattr(cli, '_ImmutableRecoveryWriter', lambda destination: destination)
    before = deepcopy(raw._documents)
    report = cli.run_once(project='synthetic', content_bucket='source', recovery_bucket='recovery',
        now=NOW, schedule_documents=raw)
    assert report['published_or_verified'] == 1
    assert raw._documents == before
    saved = recovery.list_prefix(f'recovery/v1/projects/{pid}/')[0]
    with ZipFile(BytesIO(b''.join(recovery.read_chunks(saved.name, max_bytes=saved.size)))) as archive:
        data = archive.read('project.json')
    assert b'Published' in data and b'Unpublished' not in data and b'_bm_manuscript_' not in data


def test_portable_assets_exclude_unpublished_revision_metadata_and_bytes():
    import hashlib
    from hosted.manuscript_visibility import ASSET_REVISIONS_FIELD
    from hosted.storage.keys import project_asset_key
    from tests.test_v10596_revision_images import png
    make, raw, objects, pid, _ = revision_world()
    tab = next(row for row in raw.query_collection(('projects', pid, 'tabs')) if row.data.get(HEAD_FIELD))
    active_head = tab.data[HEAD_FIELD]
    expected = []
    for color, head in [('red', active_head), ('blue', 'unpublished_asset_revision')]:
        data = png(color)
        digest = hashlib.sha256(data).hexdigest()
        key = project_asset_key(pid, digest, '.png')
        meta = objects.write_chunks(key, [data], max_bytes=len(data), content_type='image/png', if_generation_match=0)
        aid = 'asset_' + digest
        raw.run_transaction(lambda tx: tx.create(('projects', pid, 'assets', aid), {
            'id': aid, 'asset_id': aid, 'project_id': pid, 'name': 'img_' + digest[:24] + '.png',
            'sha256': digest, 'object_name': key, 'object_generation': meta.generation,
            'object_sha256': digest, 'object_size': len(data), 'generation': 1,
            ASSET_REVISIONS_FIELD: [{'tab_id': tab.path[-1], 'revision': head}],
        }))
        if head == active_head:
            expected.append('assets/' + digest + '.png')
    with actor():
        package = HostedProjectPackageService(projects_for(make().domain.documents, make().domain.objects))
        with ZipFile(BytesIO(package.export_bytes(pid, OWNER))) as archive:
            assert [name for name in archive.namelist() if name.startswith('assets/')] == expected
            assert b'_bm_manuscript_' not in archive.read('project.json')


def test_archive_read_adapter_keeps_membership_queries_scoped_and_bounded(monkeypatch):
    import sys
    from hosted.recovery_writer_cli import _ReadTransaction
    monkeypatch.setitem(sys.modules, 'google.cloud.firestore_v1.base_query',
        SimpleNamespace(FieldFilter=lambda field, op, value: (field, op, value)))
    pid = 'a' * 32
    calls = []
    snapshot = SimpleNamespace(reference=SimpleNamespace(path=f'projects/{pid}/blocks/one'),
        to_dict=lambda: {'chapter_id': 'chapter'})
    class Query:
        def where(self, **kwargs): calls.append(kwargs); return self
        def limit(self, value): calls.append(('limit', value)); return self
    client = SimpleNamespace(collection=lambda path: (calls.append(path), Query())[1])
    provider = SimpleNamespace(get=lambda query: [snapshot])
    tx = _ReadTransaction(client, provider, pid, 2)
    assert tx.query_collection_in(('projects', pid, 'blocks'), field='chapter_id', values=['chapter'])[0].path[-1] == 'one'
    assert calls == [f'projects/{pid}/blocks', {'filter': ('chapter_id', 'in', ['chapter'])}, ('limit', 3)]
    calls.clear()
    for values in ([], ['x'] * 31, [None]):
        with pytest.raises(HostedValidationError, match='MEMBERSHIP_QUERY_INVALID'):
            tx.query_collection_in(('projects', pid, 'blocks'), field='chapter_id', values=values)
    with pytest.raises(DocumentConflict, match='DOCUMENT_SCOPE_INVALID'):
        tx.query_collection_in(('projects', 'b' * 32, 'blocks'), field='chapter_id', values=['chapter'])
    assert calls == []
    provider.get = lambda query: [snapshot] * 3
    with pytest.raises(DocumentConflict, match='COLLECTION_BUDGET_EXCEEDED'):
        tx.query_collection_in(('projects', pid, 'blocks'), field='chapter_id', values=['chapter'])


def test_private_sealed_recovery_rejects_physical_revision_format_before_output():
    from hosted.encrypted_recovery import HostedEncryptedRecoveryWriter
    from tests.test_v10518_sealed_recovery_source import setup_private, OWNER as private_owner
    raw, live, recovery, kms, codec, original, pid = setup_private()
    rewrite_root(raw, pid, status='active_manuscript_v1', **{FORMAT_FIELD: 1})
    view = ManuscriptDocumentStore(raw)
    projects = HostedCloudProjectRepository(view, live, HostedProjectRepository(view),
        HostedUserRepository(HostedStructuredRepository(view)), lambda: private_owner,
        encrypted_fields=original.domain.encrypted_fields, encrypted_objects=original.domain.encrypted_objects)
    before = deepcopy(raw._documents)
    with pytest.raises(RecoveryCoverageError, match='MANUSCRIPT_REVISIONS_NOT_READY'):
        HostedEncryptedRecoveryWriter(projects, recovery, codec).create(pid, private_owner, operation='not-full-recovery')
    assert recovery.list_prefix('') == []
    assert raw._documents == before
