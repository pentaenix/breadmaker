"""M10C archive writer safety contracts; operator-owned test execution only."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hosted.firestore.contracts import DocumentConflict
from hosted.firestore.memory import MemoryDocumentStore
from hosted.operator_recovery_plan import HostedRecoveryJobPlanner, ScheduledRecoveryItem
from hosted.recovery_archives import HostedRecoveryArchiveService
from hosted.recovery_schedule import HostedScheduledRecoveryService
from hosted.recovery_writer import HostedRecoveryArchiveWriter, slot_operation
from hosted.recovery_writer_cli import required_environment, main
from hosted.runtime_services import _DEV_USER_ID
from hosted.storage.memory import MemoryObjectStore
from runtime.hosted import build_hosted_dev_runtime

NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
GCP = 'breadmaker-stage-123'
ENV = {'BREADMAKER_ENV': 'staging', 'BREADMAKER_OPERATOR_MODE': 'recovery_archive_writer',
       'BREADMAKER_GCP_PROJECT_ID': GCP, 'BREADMAKER_FIRESTORE_DATABASE': '(default)',
       'BREADMAKER_STORAGE_BUCKET': GCP + '-breadmaker-content',
       'BREADMAKER_RECOVERY_BUCKET': GCP + '-breadmaker-recovery'}


def _world(monkeypatch):
    monkeypatch.setenv('BREADMAKER_AUTH_MODE', 'developer')
    monkeypatch.setenv('BREADMAKER_ENV', 'development')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED', '1')
    docs, content, independent = MemoryDocumentStore(), MemoryObjectStore(), MemoryObjectStore()
    runtime = build_hosted_dev_runtime(documents=docs, objects=content)
    pid = runtime.projects.create('Read-only publisher sample')['id']
    archive = HostedRecoveryArchiveService(runtime.projects, independent)
    HostedScheduledRecoveryService(archive).configure(pid, _DEV_USER_ID, enabled=True, now=NOW)
    planner = HostedRecoveryJobPlanner(docs)
    def writer(max_archives=8):
        return HostedRecoveryArchiveWriter(planner, lambda item: archive, max_archives=max_archives)
    return runtime, docs, independent, pid, writer


def test_v1052_manual_writer_emits_independently_verified_archive(monkeypatch):
    runtime, docs, independent, pid, writer = _world(monkeypatch)
    result = writer().execute(now=NOW)
    assert result.published == 1 and result.archive_size_bytes > 0
    assert not result.destructive_actions_allowed
    objects = independent.list_prefix(f'recovery/v1/projects/{pid}/')
    assert len(objects) == 1 and objects[0].metadata['breadmaker-recovery-format'] == 'hosted-v3'
    # No progress or project writes: this is intentionally a read-only Firestore job.
    row = docs.get(('recovery_schedules', pid)).data
    assert row['last_archive_name'] == '' and row['next_due_epoch'] == int(NOW.timestamp())


def test_v1052_same_slot_retry_creates_no_second_archive(monkeypatch):
    _, _, independent, pid, writer = _world(monkeypatch)
    first = writer().execute(now=NOW)
    restarted = writer().execute(now=NOW + timedelta(hours=3))
    assert first.published == restarted.published == 1
    assert len(independent.list_prefix(f'recovery/v1/projects/{pid}/')) == 1


def test_v1052_next_slot_emits_distinct_archive_without_firestore_receipt(monkeypatch):
    runtime, _, independent, pid, writer = _world(monkeypatch)
    writer().execute(now=NOW)
    runtime.projects.rename(pid, 'Second day manuscript')
    second = writer().execute(now=NOW + timedelta(days=1))
    assert second.published == 1
    objects = independent.list_prefix(f'recovery/v1/projects/{pid}/')
    assert len(objects) == 2 and len({o.name for o in objects}) == 2


def test_v1052_late_schedule_does_not_burst_catchup_archives(monkeypatch):
    _, _, independent, pid, writer = _world(monkeypatch)
    result = writer().execute(now=NOW + timedelta(days=365))
    assert result.published == 1
    assert len(independent.list_prefix(f'recovery/v1/projects/{pid}/')) == 1


def test_v1052_disabled_schedule_never_publishes(monkeypatch):
    runtime, docs, independent, pid, writer = _world(monkeypatch)
    HostedScheduledRecoveryService(HostedRecoveryArchiveService(runtime.projects, independent)).configure(
        pid, _DEV_USER_ID, enabled=False, now=NOW)
    report = writer().execute(now=NOW)
    assert report.published == 0
    assert independent.list_prefix(f'recovery/v1/projects/{pid}/') == []


def test_v1052_fails_closed_on_changed_membership(monkeypatch):
    _, docs, independent, pid, writer = _world(monkeypatch)
    row = docs.get(('projects', pid, 'members', _DEV_USER_ID)).data
    docs.run_transaction(lambda tx: tx.set(('projects', pid, 'members', _DEV_USER_ID), {**row, 'role': 'editor'}))
    with pytest.raises(DocumentConflict, match='OWNER_OR_PROJECT_CHANGED'):
        writer().execute(now=NOW)
    assert independent.list_prefix(f'recovery/v1/projects/{pid}/') == []


def test_v1052_fails_closed_when_plan_drifts_before_publishing(monkeypatch):
    runtime, docs, independent, pid, writer = _world(monkeypatch)
    original = writer()
    def changed_archive(item):
        raise AssertionError('archive factory must not be called')
    original.archive_for_item = changed_archive
    original_revalidate = original.planner.revalidate
    def change_then_validate(plan, *, now):
        schedule = dict(docs.get(('recovery_schedules', pid)).data)
        docs.run_transaction(lambda tx: tx.set(('recovery_schedules', pid), {
            **schedule, 'enabled': False,
        }))
        return original_revalidate(plan, now=now)
    original.planner.revalidate = change_then_validate
    with pytest.raises(DocumentConflict, match='SCHEDULE_DRIFT'):
        original.execute(now=NOW)
    assert independent.list_prefix(f'recovery/v1/projects/{pid}/') == []


def test_v1052_budget_fails_closed_before_any_publication(monkeypatch):
    _, docs, independent, pid, writer = _world(monkeypatch)
    with pytest.raises(ValueError, match='BUDGET_INVALID'):
        writer(max_archives=9)
    # Force zero budget is invalid even if currently only one project is due.
    with pytest.raises(ValueError, match='BUDGET_INVALID'):
        writer(max_archives=0)


def test_v1052_slot_op_rejects_naive_time_and_future_due():
    item = ScheduledRecoveryItem('a'*32, _DEV_USER_ID, int(NOW.timestamp()), 1, 24)
    assert slot_operation(item, now=NOW, cadence_hours=24) == slot_operation(
        item, now=NOW + timedelta(hours=23), cadence_hours=24)
    assert slot_operation(item, now=NOW, cadence_hours=24) != slot_operation(
        item, now=NOW + timedelta(hours=24), cadence_hours=24)
    with pytest.raises(DocumentConflict, match='UTC_REQUIRED'):
        slot_operation(item, now=datetime(2026, 10, 12), cadence_hours=24)
    with pytest.raises(DocumentConflict, match='NOT_DUE'):
        slot_operation(item, now=NOW - timedelta(seconds=1), cadence_hours=24)


def test_v1052_cli_rejects_production_or_unsafe_buckets_before_provider(capsys):
    for changed in ({'BREADMAKER_ENV': 'production'},
                    {'BREADMAKER_OPERATOR_MODE': 'recovery_plan_read_only'},
                    {'BREADMAKER_RECOVERY_BUCKET': GCP + '-breadmaker-content'},
                    {'BREADMAKER_STORAGE_BUCKET': 'some-other-bucket'},
                    {'BREADMAKER_FIRESTORE_DATABASE': 'other-db'}):
        with pytest.raises(ValueError):
            required_environment({**ENV, **changed})
        assert main(['archive'], env={**ENV, **changed}, run=lambda **kw: 1) == 2
    assert 'BLOCKED' in capsys.readouterr().err


def test_v1052_cli_redacts_identity_and_invokes_bounded_runner(capsys):
    called = []
    def fake_run(**kwargs):
        called.append(kwargs)
        return {'inspected': 1, 'published_or_verified': 1, 'fingerprint': 'f'*64,
                'destructive_actions_allowed': False}
    assert main(['archive'], env=ENV, run=fake_run) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['result'] == 'ARCHIVES_VERIFIED'
    assert called == [{'project': GCP, 'content_bucket': ENV['BREADMAKER_STORAGE_BUCKET'],
                       'recovery_bucket': ENV['BREADMAKER_RECOVERY_BUCKET']}]
    assert _DEV_USER_ID not in json.dumps(report)


def test_v1052_image_is_allowlisted_and_has_no_web_listener():
    path = Path(__file__).resolve().parents[1] / 'deploy/Dockerfile.hosted-recovery-writer'
    src = path.read_text()
    assert 'USER 10001:10001' in src and 'COPY src ./src' in src
    assert 'COPY . ' not in src and 'uvicorn' not in src
    assert 'hosted.recovery_writer_cli' in src


def test_v1052_archive_initial_upload_requires_create_only_and_never_patches():
    import hashlib
    from hosted.recovery_writer_cli import _ImmutableRecoveryWriter
    from hosted.storage.contracts import ObjectMetadata
    payload = b'hosted-v3 immutable bytes'
    digest = hashlib.sha256(payload).hexdigest()
    name = 'recovery/v1/projects/' + 'a'*32 + '/snap_readback.breadmaker'
    class Blob:
        def __init__(self):
            self.name, self.metadata, self.generation = name, {}, None
        def upload_from_string(self, data, *, content_type, if_generation_match, checksum):
            assert data == payload and if_generation_match == 0 and checksum == 'auto'
            assert self.metadata['breadmaker-sha256'] == digest
            self.generation = 7
        def patch(self, **kwargs):
            raise AssertionError('writer must not require storage.objects.update')
    class Bucket:
        name = 'independent-archive'
        def __init__(self): self.one = Blob()
        def blob(self, requested):
            assert requested == name
            return self.one
    class Store:
        def __init__(self): self._bucket = Bucket()
        def _translate(self, error): return error
        def stat(self, requested):
            assert requested == name
            return ObjectMetadata(name=name, generation=7, size=len(payload), sha256=digest,
                                  content_type='application/zip',
                                  metadata={'breadmaker-sha256': digest})
    storage = _ImmutableRecoveryWriter(Store())
    meta = storage.write_chunks(name, [payload], max_bytes=1024, content_type='application/zip',
                                if_generation_match=0, metadata={'breadmaker-sha256': digest})
    assert meta.generation == 7 and meta.sha256 == digest
    assert not hasattr(storage, 'delete')


def test_v1052_archive_writer_rejects_mutating_existing_generation():
    from hosted.recovery_writer_cli import _ImmutableRecoveryWriter
    from hosted.storage.contracts import ObjectLimitExceeded
    class FakeStore:
        _bucket = object()
    writer = _ImmutableRecoveryWriter(FakeStore())
    with pytest.raises(DocumentConflict, match='IMMUTABLE_CREATE_REQUIRED'):
        writer.write_chunks('recovery/v1/projects/' + 'a'*32 + '/bad', [b'no'],
                            max_bytes=100, content_type='application/zip', if_generation_match=4,
                            metadata={'breadmaker-sha256': '0'*64})
    with pytest.raises(ObjectLimitExceeded, match='OBJECT_BUDGET_INVALID'):
        writer.write_chunks('recovery/v1/projects/' + 'a'*32 + '/bad', [b'no'],
                            max_bytes=999_999_999, content_type='application/zip', if_generation_match=0,
                            metadata={'breadmaker-sha256': '0'*64})
