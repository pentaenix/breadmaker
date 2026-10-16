from __future__ import annotations

from storage import context as storage_context
from storage import history as storage_history
from storage import mutations as storage_mutations
from storage import recovery as storage_recovery
from storage import schema_contracts as storage_schema_contracts
from storage import values as storage_values

import json
from pathlib import Path

import pytest
from revision_store.local_store import LocalRevisionStore, RevisionStoreCorruption


def _workspace_id(root: Path) -> str:
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    return str(manifest['project_id'])


def test_v7752_new_revision_manifests_bind_portable_workspace_identity(fresh_project):
    assert storage_context.PROJECT_ROOT is not None
    root=Path(storage_context.PROJECT_ROOT).resolve()
    workspace_id=_workspace_id(root)
    assert len(workspace_id)==32

    store=storage_history.local_revision_store()
    row=store.capture(label='7.7.5.2 workspace binding',creation_reason='identity-test',force_checkpoint=True)
    assert row['workspace_project_id']==workspace_id

    revision_path=next(p for p in store.revisions_dir.glob('*.revision.json') if row['revision_id'] in p.name)
    revision=json.loads(revision_path.read_text(encoding='utf-8'))
    checkpoint_path=next(p for p in store.checkpoints_dir.glob('*.checkpoint.json') if revision['checkpoint_id'] in p.name)
    checkpoint=json.loads(checkpoint_path.read_text(encoding='utf-8'))
    snapshot=json.loads((store.objects_dir/revision['snapshot_object_id']/'snapshot_manifest.json').read_text(encoding='utf-8'))

    assert revision['workspace_project_id']==workspace_id
    assert checkpoint['workspace_project_id']==workspace_id
    assert snapshot['workspace_project_id']==workspace_id
    assert store.verify_revision(row['revision_id'])['revision']['workspace_project_id']==workspace_id


def test_v7752_revision_repository_transplant_is_rejected_even_when_sqlite_project_id_matches(fresh_project):
    assert storage_context.PROJECT_ROOT is not None
    root=Path(storage_context.PROJECT_ROOT).resolve()
    workspace_id=_workspace_id(root)
    store=storage_history.local_revision_store()
    row=store.capture(label='Bound history',creation_reason='identity-test',force_checkpoint=True)

    # SQLite projects.id is typically 1 in every separate Breadmaker project. Simulate
    # the exact dangerous transplant case by opening these valid immutable History bytes
    # under another workspace UUID while keeping the same DB/local project row id.
    other_id=('0' if workspace_id[0] != '0' else '1') + workspace_id[1:]
    # 7.7.9 may reject the transplant even earlier at the frozen repository-root
    # identity fence. Earlier failure is strictly stronger than allowing construction
    # and discovering the mismatch only while verifying a revision.
    with pytest.raises(RevisionStoreCorruption, match='HISTORY_(?:STORE_)?WORKSPACE_PROJECT_ID_MISMATCH'):
        transplanted=LocalRevisionStore(
            store.root,
            storage_context.DB_PATH,
            storage_context.ASSET_DIR,
            canonical_tables=storage_schema_contracts._GENERATION_CANONICAL_TABLES,
            checkpoint_interval=store.checkpoint_interval,
            workspace_project_id=other_id,
        )
        transplanted.verify_revision(row['revision_id'])


def test_v7752_history_repository_without_workspace_identity_is_rejected(fresh_project, tmp_path):
    # 7.8 intentionally dropped pre-freeze identity-less History compatibility.
    # Current History must always be bound to the immutable workspace UUID.
    with pytest.raises(RevisionStoreCorruption, match='HISTORY_WORKSPACE_PROJECT_ID_REQUIRED'):
        LocalRevisionStore(
            tmp_path/'identityless-history',
            storage_context.DB_PATH,
            storage_context.ASSET_DIR,
            canonical_tables=storage_schema_contracts._GENERATION_CANONICAL_TABLES,
            checkpoint_interval=3,
        )


def _advance_title_for_restart_tracking() -> int:
    with storage_mutations.tx(operation='v7752-restart-tracking') as con:
        row=con.execute('SELECT id,title FROM projects ORDER BY id LIMIT 1').fetchone()
        con.execute('UPDATE projects SET title=?,updated_at=? WHERE id=?',
                    (str(row[1])+' tracked',storage_values.now(),int(row[0])))
    return storage_mutations.current_project_generation()


def test_v7752_restart_requeues_generation_that_committed_before_recovery_publication(fresh_project):
    assert storage_context.PROJECT_ROOT is not None
    root=Path(storage_context.PROJECT_ROOT).resolve()
    baseline=storage_mutations.current_project_generation()
    # Activation establishes the one-time tracking baseline without claiming a package.
    assert storage_recovery.recovery_scheduler_protected_generation(root,baseline,initialize=True)==baseline

    advanced=_advance_title_for_restart_tracking()
    protected=storage_recovery.recovery_scheduler_protected_generation(root,advanced,initialize=True)
    assert protected==baseline

    from backups.scheduler import RecoveryBackupScheduler
    scheduler=RecoveryBackupScheduler(quiet_seconds=2,long_session_seconds=60,retry_seconds=5)
    scheduler.reset(str(fresh_project['project']['id']),advanced,100,protected_generation=protected)
    assert scheduler.last_success_generation==baseline
    assert scheduler.dirty_generation==advanced
    assert scheduler.due(101.9) is None
    decision=scheduler.due(102.0)
    assert decision is not None and decision.trigger=='quiet_session' and decision.generation==advanced


def test_v7752_verified_local_recovery_survives_restart_as_scheduler_high_water(fresh_project):
    assert storage_context.PROJECT_ROOT is not None
    root=Path(storage_context.PROJECT_ROOT).resolve()
    baseline=storage_mutations.current_project_generation()
    storage_recovery.recovery_scheduler_protected_generation(root,baseline,initialize=True)
    advanced=_advance_title_for_restart_tracking()
    package=storage_recovery.backup_project('v7752_restart_protection',project_root=root)
    assert package is not None and package.is_file()

    protected=storage_recovery.recovery_scheduler_protected_generation(root,advanced,initialize=True)
    assert protected==advanced
    from backups.scheduler import RecoveryBackupScheduler
    scheduler=RecoveryBackupScheduler(quiet_seconds=0,long_session_seconds=0,retry_seconds=5)
    scheduler.reset(str(fresh_project['project']['id']),advanced,100,protected_generation=protected)
    assert scheduler.dirty_generation==0
    assert scheduler.due(100) is None


def test_v7752_missing_recovery_tracking_cache_fails_toward_more_backup_not_less(fresh_project):
    assert storage_context.PROJECT_ROOT is not None
    root=Path(storage_context.PROJECT_ROOT).resolve()
    baseline=storage_mutations.current_project_generation()
    storage_recovery.recovery_scheduler_protected_generation(root,baseline,initialize=True)
    advanced=_advance_title_for_restart_tracking()
    package=storage_recovery.backup_project('v7752_tracking_cache',project_root=root)
    assert package is not None
    state=root/'backups'/'recovery_state.json'
    assert state.is_file()
    state.unlink()
    # 7.8 moves the baseline out of reconstructible manifest.json. Losing the
    # complete safety record is uncertainty and therefore returns no claimed
    # protection; this can only cause an extra backup.
    assert storage_recovery.recovery_scheduler_protected_generation(root,advanced,initialize=True)==0
