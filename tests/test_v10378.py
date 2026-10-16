"""M9 Recovery bundle: operator-only full verifier tests (NEVER run by model).

Credential-free but exercised only by the human release harness. No live GCP.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hosted.firestore.contracts import DocumentConflict, HostedAuthorizationError
from hosted.firestore.memory import MemoryDocumentStore
from hosted.recovery_archives import HostedRecoveryArchiveService
from hosted.recovery_schedule import HostedScheduledRecoveryService, RecoveryScheduleError
from hosted.recovery_drills import HostedRecoveryDrillService, RecoveryDrillError
from hosted.runtime_services import _DEV_USER_ID, ensure_hosted_user
from hosted.storage.contracts import ObjectIntegrityError
from hosted.storage.memory import MemoryObjectStore
from runtime.hosted import build_hosted_dev_runtime

START = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


def _fixture(monkeypatch):
    monkeypatch.setenv('BREADMAKER_AUTH_MODE', 'developer')
    monkeypatch.setenv('BREADMAKER_ENV', 'development')
    monkeypatch.setenv('BREADMAKER_HOSTED_DEV_ALLOW_UNAUTHENTICATED', '1')
    documents, content, independent = MemoryDocumentStore(), MemoryObjectStore(), MemoryObjectStore()
    runtime = build_hosted_dev_runtime(documents=documents, objects=content)
    pid = runtime.projects.create('Offline recoverable manuscript')['id']
    archive = HostedRecoveryArchiveService(runtime.projects, independent)
    return runtime, documents, content, independent, pid, archive


def test_v10378_daily_job_creates_one_verified_snapshot_and_skips_until_due(monkeypatch):
    runtime, documents, content, independent, pid, archive = _fixture(monkeypatch)
    schedule = HostedScheduledRecoveryService(archive)
    config = schedule.configure(pid, _DEV_USER_ID, enabled=True, now=START)
    assert config['retention_days'] >= 30
    first = schedule.tick(pid, _DEV_USER_ID, now=START)
    assert first.due and first.verified and first.archive is not None
    assert first.archive.project_id == pid
    assert schedule.check_last(pid, _DEV_USER_ID) == first.archive
    skipped = schedule.tick(pid, _DEV_USER_ID, now=START + timedelta(hours=4))
    assert not skipped.due and skipped.archive is None
    assert len(independent.list_prefix(f'recovery/v1/projects/{pid}/')) == 1
    runtime.projects.rename(pid, 'Revised after first daily snapshot')
    next_day = schedule.tick(pid, _DEV_USER_ID, now=START + timedelta(days=1))
    assert next_day.due and next_day.archive.project_generation > first.archive.project_generation
    assert len(independent.list_prefix(f'recovery/v1/projects/{pid}/')) == 2
    assert schedule.check_last(pid, _DEV_USER_ID) == next_day.archive


def test_v10378_schedule_is_restart_safe_with_shared_stores(monkeypatch):
    runtime, documents, content, independent, pid, archive = _fixture(monkeypatch)
    HostedScheduledRecoveryService(archive).configure(pid, _DEV_USER_ID, enabled=True, now=START)
    first = HostedScheduledRecoveryService(archive).tick(pid, _DEV_USER_ID, now=START)
    replacement_runtime = build_hosted_dev_runtime(documents=documents, objects=content)
    replacement = HostedScheduledRecoveryService(HostedRecoveryArchiveService(replacement_runtime.projects, independent))
    assert replacement.check_last(pid, _DEV_USER_ID) == first.archive
    assert not replacement.tick(pid, _DEV_USER_ID, now=START + timedelta(minutes=1)).due


def test_v10378_scheduler_refuses_collaborator_and_invalid_policy(monkeypatch):
    runtime, documents, content, independent, pid, archive = _fixture(monkeypatch)
    outsider = 'usr_' + '2'*32
    ensure_hosted_user(documents, user_id=outsider, display_name='Reader')
    expected = runtime.projects.get(pid)['generation']
    runtime.projects.authority.add_member(pid, _DEV_USER_ID, outsider, 'editor',
        expected_project_generation=expected, operation_id='m9d_second_account')
    schedule = HostedScheduledRecoveryService(archive)
    with pytest.raises(HostedAuthorizationError):
        schedule.configure(pid, outsider, enabled=True, now=START)
    with pytest.raises(RecoveryScheduleError, match='RETENTION_TOO_SHORT'):
        schedule.configure(pid, _DEV_USER_ID, enabled=True, now=START, retention_days=7)
    with pytest.raises(RecoveryScheduleError, match='UTC_INSTANT_REQUIRED'):
        schedule.configure(pid, _DEV_USER_ID, enabled=True, now=datetime(2026,10,11,12))
    with pytest.raises(RecoveryScheduleError, match='SCHEDULE_DISABLED'):
        schedule.tick(pid, _DEV_USER_ID, now=START)
    assert independent.list_prefix(f'recovery/v1/projects/{pid}/') == []


def test_v10378_archive_tamper_breaks_schedule_receipt_validation(monkeypatch):
    runtime, _, _, independent, pid, archive = _fixture(monkeypatch)
    schedule = HostedScheduledRecoveryService(archive)
    schedule.configure(pid, _DEV_USER_ID, enabled=True, now=START)
    first = schedule.tick(pid, _DEV_USER_ID, now=START).archive
    meta = independent.stat(first.object_name)
    independent.delete(meta.name, if_generation_match=meta.generation)
    independent.write_chunks(meta.name, [b'corrupted'], max_bytes=128,
        if_generation_match=0, content_type=meta.content_type, metadata=meta.metadata)
    with pytest.raises((DocumentConflict, ObjectIntegrityError)):
        schedule.check_last(pid, _DEV_USER_ID)


def test_v10378_isolated_restore_verifies_full_project_content(monkeypatch):
    runtime, _, content, independent, pid, archive = _fixture(monkeypatch)
    block = runtime.projects.domain.list('manuscript_block', {'project_id':pid}, user_id=_DEV_USER_ID)[0]
    runtime.projects.domain.update('manuscript_block', {'project_id':pid,'block_id':block['id']},
        {'content_html':'<p>Unique verified passage</p>', 'text_content':'Unique verified passage'},
        user_id=_DEV_USER_ID, operation='m9d_source_text')
    frozen = archive.create(pid,_DEV_USER_ID,operation='drill_1')
    result = HostedRecoveryDrillService(archive).drill_archive(pid,_DEV_USER_ID,operation='drill_1')
    assert result.isolated and result.semantic_match and result.archive_sha256 == frozen.sha256
    assert result.restored_project_id != pid
    assert ('manuscript_block',1) in result.entity_counts
    assert len(independent.list_prefix(f'recovery/v1/projects/{pid}/')) == 1
    assert runtime.projects.domain.list('manuscript_block', {'project_id':pid}, user_id=_DEV_USER_ID)[0]['text_content'] == 'Unique verified passage'


def test_v10378_drill_survives_complete_source_firestore_loss(monkeypatch):
    runtime, documents, content, independent, pid, archive = _fixture(monkeypatch)
    frozen = archive.create(pid,_DEV_USER_ID,operation='disaster')
    # Simulate total loss of original live Firestore; the independent recovery
    # bucket alone still holds the verified author snapshot.
    documents._documents.clear()
    result = HostedRecoveryDrillService(archive).offline_drill(pid,_DEV_USER_ID,operation='disaster')
    assert result.semantic_match and result.archive_sha256 == frozen.sha256
    assert result.restored_project_id != pid
    assert independent.stat(frozen.object_name) is not None


def test_v10378_offline_drill_rejects_changed_or_missing_archive(monkeypatch):
    runtime, _, _, independent, pid, archive = _fixture(monkeypatch)
    frozen = archive.create(pid,_DEV_USER_ID,operation='corrupt')
    meta = independent.stat(frozen.object_name)
    independent.delete(meta.name, if_generation_match=meta.generation)
    with pytest.raises(RecoveryDrillError, match='ARCHIVE_MISSING'):
        HostedRecoveryDrillService(archive).offline_drill(pid,_DEV_USER_ID,operation='corrupt')
    independent.write_chunks(meta.name,[b'bad'],max_bytes=128,
        content_type=meta.content_type,if_generation_match=0,metadata=meta.metadata)
    with pytest.raises(ObjectIntegrityError):
        HostedRecoveryDrillService(archive).offline_drill(pid,_DEV_USER_ID,operation='corrupt')


def test_v10378_schedule_failure_does_not_claim_success(monkeypatch):
    runtime, documents, content, independent, pid, archive = _fixture(monkeypatch)
    schedule = HostedScheduledRecoveryService(archive)
    schedule.configure(pid, _DEV_USER_ID, enabled=True, now=START)
    original = archive.projects.domain.list
    def fail_one(entity, *args, **kwargs):
        if entity == 'chapter':
            raise RuntimeError('SOURCE_READ_ABORTED')
        return original(entity, *args, **kwargs)
    monkeypatch.setattr(archive.projects.domain, 'list', fail_one)
    with pytest.raises(RuntimeError, match='SOURCE_READ_ABORTED'):
        schedule.tick(pid,_DEV_USER_ID,now=START)
    assert documents.get(('recovery_schedules',pid)).data['last_archive_name'] == ''
    assert independent.list_prefix(f'recovery/v1/projects/{pid}/') == []
    monkeypatch.setattr(archive.projects.domain, 'list', original)
    assert schedule.tick(pid,_DEV_USER_ID,now=START).verified


def test_v10378_no_public_backup_deletion_capability(monkeypatch):
    runtime, _, _, independent, pid, archive = _fixture(monkeypatch)
    assert not hasattr(archive, 'delete')
    assert not hasattr(HostedScheduledRecoveryService(archive),'delete_archive')
    assert not hasattr(HostedRecoveryDrillService(archive),'delete_archive')
    # M10 grants write-once/read-only recovery IAM; physical purge remains gated.


def test_v10378_portability_audit_reports_historical_gap_without_erasing_archive(monkeypatch):
    runtime, _, _, independent, pid, archive = _fixture(monkeypatch)
    from hosted.portability_audit import HostedPortabilityAuditService
    from hosted.packages import HostedProjectPackageService
    audit = HostedPortabilityAuditService(runtime.projects)
    basic = audit.audit(pid,_DEV_USER_ID)
    assert basic.local_schema42_compatible and basic.local_export_blocker is None
    assert basic.local_package_sha256 is not None
    canvas = runtime.projects.domain.create('canvas',{'project_id':pid},
        {'name':'Frozen graph','position':0},user_id=_DEV_USER_ID)
    version = runtime.projects.domain.create('canvas_version',{'project_id':pid,'canvas_id':canvas['id']},
        {'name':'Snapshot'},user_id=_DEV_USER_ID)
    runtime.projects.domain.create('canvas_version_item',
        {'project_id':pid,'canvas_id':canvas['id'],'version_id':version['id']},
        {'kind':'node','source_id':'node_1','payload':{'x':10,'y':20}},user_id=_DEV_USER_ID)
    gap = audit.audit(pid,_DEV_USER_ID)
    assert not gap.local_schema42_compatible
    assert gap.local_export_blocker == 'HOSTED_PORTABLE_CANVAS_VERSION_SHAPE_UNSUPPORTED'
    assert gap.local_package_sha256 is None
    # Hosted-v3 is still independently archivable even though Schema-42 cannot
    # express this frozen geometry history. No "successful" lossy export.
    recovered = archive.create(pid,_DEV_USER_ID,operation='rich_history_backup')
    assert recovered.size > 0
    import io, json, zipfile
    with zipfile.ZipFile(io.BytesIO(archive.read(pid,_DEV_USER_ID,operation='rich_history_backup'))) as restored:
        assert any(row['data'].get('name') == 'Frozen graph' for row in json.loads(restored.read('project.json'))['entities']['canvas'])


def test_v10378_crash_after_publish_replays_same_immutable_archive(monkeypatch):
    runtime, documents, content, independent, pid, archive = _fixture(monkeypatch)
    schedule = HostedScheduledRecoveryService(archive)
    schedule.configure(pid, _DEV_USER_ID, enabled=True, now=START)
    original = archive.create
    created = []
    def crash_after_publish(*args, **kwargs):
        created.append(original(*args, **kwargs))
        raise RuntimeError('WORKER_TERMINATED_AFTER_PUBLISH')
    monkeypatch.setattr(archive,'create',crash_after_publish)
    with pytest.raises(RuntimeError, match='WORKER_TERMINATED_AFTER_PUBLISH'):
        schedule.tick(pid,_DEV_USER_ID,now=START)
    assert documents.get(('recovery_schedules',pid)).data['last_archive_name'] == ''
    assert len(independent.list_prefix(f'recovery/v1/projects/{pid}/')) == 1
    monkeypatch.setattr(archive,'create',original)
    replay = schedule.tick(pid,_DEV_USER_ID,now=START)
    assert replay.verified and replay.archive == created[0]
    assert len(independent.list_prefix(f'recovery/v1/projects/{pid}/')) == 1


def test_v10378_project_edit_after_archive_prevents_false_fresh_receipt(monkeypatch):
    runtime, docs, content, independent, pid, archive = _fixture(monkeypatch)
    schedule = HostedScheduledRecoveryService(archive)
    schedule.configure(pid,_DEV_USER_ID,enabled=True,now=START)
    original = archive.create
    def race(*args, **kwargs):
        record = original(*args, **kwargs)
        runtime.projects.rename(pid,'Edited after archived snapshot')
        return record
    monkeypatch.setattr(archive,'create',race)
    with pytest.raises(DocumentConflict, match='HOSTED_SCHEDULE_CONCURRENT_PROJECT_EDIT'):
        schedule.tick(pid,_DEV_USER_ID,now=START)
    receipt = docs.get(('recovery_schedules',pid)).data
    assert receipt['last_archive_name'] == '' and receipt['next_due_epoch'] == int(START.timestamp())
    assert len(independent.list_prefix(f'recovery/v1/projects/{pid}/')) == 1
