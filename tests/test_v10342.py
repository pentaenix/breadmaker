from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'src'
if str(SRC) not in sys.path:sys.path.insert(0,str(SRC))


def read(path:str)->str:
    return (ROOT/path).read_text(encoding='utf-8')


def _class_block(source:str,name:str,next_name:str)->str:
    start=source.index(f'class {name}')
    end=source.index(f'class {next_name}',start)
    return source[start:end]


def test_v10342_release_and_local_schema_boundary():
    assert tuple(map(int, read('VERSION').strip().split("."))) >= (10, 3, 42)
    assert 'APP_SCHEMA_VERSION = 42' in read('src/workspace/constants.py')
    assert 'SCHEMA_VERSION = 42' in read('src/storage/schema.py')


def test_v10342_environment_neutral_ports_do_not_require_paths():
    contracts=read('src/runtime/contracts.py')
    project=_class_block(contracts,'ProjectRepository','NativeProjectStore')
    assets=_class_block(contracts,'AssetStore','NativeAssetStore')
    usage=_class_block(contracts,'ProjectUsageService','CapabilityProvider')
    assert 'Path' not in project
    assert 'Path' not in assets
    assert 'Path' not in usage
    for name in ('NativeProjectStore','NativeAssetStore','NativeHistoryStore','NativeBackupStore','ProjectUsageService','StoredAsset'):
        assert f'class {name}' in contracts
    runtime_block=contracts[contracts.index('class RuntimeServices'):]
    for field in ('native_projects','native_assets','native_history','native_backups'):
        assert f'{field}:' in runtime_block and '| None' in runtime_block


def test_v10342_local_runtime_supplies_ports_without_cloud_dependencies():
    local=read('src/runtime/local.py')
    requirements=read('requirements.txt').lower()
    assert 'google-cloud-firestore' not in requirements
    assert 'google-cloud-storage' not in requirements
    assert 'firebase' not in requirements
    assert 'LocalProjectUsageService' in local
    assert 'native_projects=projects' in local
    assert 'native_assets=assets' in local
    assert 'native_history=history' in local
    assert 'native_backups=backups' in local
    assert 'def avatar_bytes(' in local
    assert 'StoredAsset(' in local


def test_v10342_cross_cutting_http_edges_use_ports_not_paths():
    request_context=read('src/backend/request_context.py')
    projects=read('src/api/project_management/projects.py')
    assets=read('src/api/project_management/assets.py')
    uploads=read('src/api/import_export/assets.py')
    profiles=read('src/api/project_management/profiles.py')
    assert 'from workspace import workspace' not in request_context
    assert 'runtime.usage.owner_project_count' in request_context
    assert 'runtime.usage.owner_storage_bytes' in request_context
    assert 'runtime.usage.project_storage_bytes' in request_context
    assert 'from storage import ' not in projects
    assert 'runtime.projects.rename' in projects
    assert 'storage_mutations' not in projects and 'storage_database' not in projects
    assert 'runtime.assets.read_blob' in assets
    assert 'storage_context' not in assets
    assert 'runtime.assets.put_blob' in uploads
    assert '.stage_upload(' not in uploads and '.finalize_staged(' not in uploads
    assert 'runtime.identity.avatar_bytes' in profiles
    assert 'runtime.identity.avatar_path' not in profiles


def test_v10342_native_path_debt_is_explicit_and_inventory_is_exact():
    native=read('src/runtime/native.py')
    for fn in ('require_native_projects','require_native_assets','require_native_history','require_native_backups'):
        assert f'def {fn}' in native

    spec=importlib.util.spec_from_file_location('bm_hosted_inventory',ROOT/'scripts'/'generate_hosted_local_dependency_inventory.py')
    module=importlib.util.module_from_spec(spec);assert spec and spec.loader;spec.loader.exec_module(module)
    expected=json.loads(read('docs/HOSTED_LOCAL_DEPENDENCY_INVENTORY.json'))
    assert module.collect()==expected
    assert expected['local_engine_dependencies']
    assert 'may only shrink' in expected['rule']


def test_v10342_milestone_one_seam_document_records_deferred_domain_migration():
    doc=read('docs/HOSTED_LOCAL_PERSISTENCE_SEAMS.md')
    assert 'Milestone 1 does **not** invent fake Firestore-shaped CRUD interfaces' in doc
    assert 'future Cloud Run composition must leave them `None`' in doc
    assert 'model does **not** run it' in doc
