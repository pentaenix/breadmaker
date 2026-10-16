from __future__ import annotations

import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'src'


def test_v10226_final_backend_ownership_packages_have_no_flat_facades():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (10,2,26)
    assert (SRC/'api/workspace_tools').is_dir() and not (SRC/'api/workspace_tools.py').exists()
    assert (SRC/'formats/project_json').is_dir() and not (SRC/'formats/project_json.py').exists()
    assert (SRC/'workspace/projects').is_dir() and not (SRC/'workspace/projects.py').exists()
    assert not (SRC/'backend/contracts.py').exists()


def test_v10226_workspace_tools_and_project_json_boundaries_are_owner_only():
    wt=SRC/'api/workspace_tools'
    assert {'references.py','bookmark.py','ui_state.py','documents.py','contracts.py','README.md','__init__.py'} <= {p.name for p in wt.iterdir()}
    boundary=ast.parse((wt/'__init__.py').read_text(encoding='utf-8'))
    assert not [n for n in boundary.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
    pj=SRC/'formats/project_json'
    assert {'validation.py','markup.py','layout.py','importer.py','exporter.py','README.md','__init__.py'} <= {p.name for p in pj.iterdir()}
    pj_boundary=ast.parse((pj/'__init__.py').read_text(encoding='utf-8'))
    assert not [n for n in pj_boundary.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]


def test_v10226_workspace_projects_are_composed_without_flat_service_module():
    projects=SRC/'workspace/projects'
    assert {'recovery.py','registry.py','packages.py','trash.py','README.md','__init__.py'} <= {p.name for p in projects.iterdir()}
    boundary=(projects/'__init__.py').read_text(encoding='utf-8')
    for owner in ('ProjectRecoveryMixin','ProjectRegistryMixin','ProjectPackageMixin','ProjectTrashMixin'):
        assert owner in boundary
    assert 'def export_project(' not in boundary
    assert 'def delete_project(' not in boundary


def test_v10226_http_payload_contracts_live_with_domains_not_backend_registry():
    required={
        'api/project_management/contracts.py':'DangerConfirm',
        'api/manuscript/contracts.py':'BlocksUpdate',
        'api/reviews/contracts.py':'ReviewSelectionIn',
        'api/tasks/contracts.py':'TaskCreate',
        'api/canvas/contracts.py':'CanvasCreate',
        'api/durability/contracts.py':'HistoryRestoreRequest',
        'api/checkpoints/contracts.py':'CheckpointApply',
        'api/import_export/contracts.py':'PDFExportOptions',
        'api/maps/contracts.py':'MapCreate',
        'api/workspace_tools/contracts.py':'BookmarkSet',
        'api/shared_contracts.py':'class BaseModel',
    }
    for rel,symbol in required.items():
        assert symbol in (SRC/rel).read_text(encoding='utf-8'), rel
    offenders=[]
    for path in SRC.rglob('*.py'):
        text=path.read_text(encoding='utf-8')
        if 'backend.contracts' in text:
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders==[]


def test_v10226_cross_domain_callers_use_new_canonical_owners():
    maps=(SRC/'api/maps/anchors.py').read_text(encoding='utf-8')
    assert "feature_call('workspace_tools.references', '_describe_ref')" in maps
    assert "feature_call('workspace_tools.references', '_entity_exists')" in maps
    json_transfer=(SRC/'api/project_management/json_transfer.py').read_text(encoding='utf-8')
    assert 'formats.project_json import validation as project_json_validation' in json_transfer
    assert 'formats.project_json import importer as project_json_importer' in json_transfer
    assert 'formats.project_json import exporter as project_json_exporter' in json_transfer
