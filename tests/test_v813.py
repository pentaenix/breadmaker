from __future__ import annotations

from workspace import workspace

from api.durability import backups as backup_api
from source_contracts import backend_source, storage_source

from pathlib import Path

import app as app_module
from backend import recovery_runtime

ROOT=Path(__file__).resolve().parents[1]


def test_v813_release_and_no_schema_migration_runtime():
    version=ROOT.joinpath('VERSION').read_text(encoding='utf-8').strip()
    assert tuple(map(int,version.split('.'))) >= (8,1,3)
    storage=storage_source(ROOT)
    schema=ROOT.joinpath('src','storage','schema.py').read_text(encoding='utf-8')
    app=backend_source(ROOT)
    workspace='\n'.join(path.read_text(encoding='utf-8') for path in sorted(ROOT.joinpath('src','workspace').glob('*.py')))
    combined='\n'.join((storage,schema,app,workspace))
    assert 'upgrade_project_schema_22_to_23' not in combined
    assert 'SCHEMA_22_TO_23' not in combined
    assert 'manifest_schema in {22' not in combined
    assert 'database_schema in {22' not in combined


def test_v813_recovery_backup_dir_can_resolve_failed_project_without_storage_binding(tmp_path,monkeypatch):
    failed=tmp_path/'projects'/'failed'; (failed/'backups').mkdir(parents=True)
    monkeypatch.setattr(recovery_runtime,'_RECOVERY_MODE',{'active_project_id':'failed'})
    monkeypatch.setattr(workspace,'recovery_candidates',lambda:[{'id':'failed','path':str(failed),'database_exists':True,'readable':False}])
    assert backup_api._available_recovery_backup_dir()==failed/'backups'
