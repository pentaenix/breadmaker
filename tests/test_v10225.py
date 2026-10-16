from __future__ import annotations

import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
IMPORTS=ROOT/'src/api/import_export'
BACKUPS=ROOT/'src/workspace/backups'


def test_v10225_import_export_is_explicit_package_without_flat_facade():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (10,2,25)
    assert IMPORTS.is_dir() and not (ROOT/'src/api/import_export.py').exists()
    required={'__init__.py','README.md','docx_parser.py','uploads.py','docx_import.py','docx_export.py','pdf_export.py','assets.py'}
    assert required <= {p.name for p in IMPORTS.iterdir()}
    tree=ast.parse((IMPORTS/'__init__.py').read_text(encoding='utf-8'))
    assert not [n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
    assert 'router.include_router(_owner.router)' in (IMPORTS/'__init__.py').read_text(encoding='utf-8')


def test_v10225_import_export_cross_domain_upload_edges_use_owner():
    cuts=(ROOT/'src/api/durability/cuts.py').read_text(encoding='utf-8')
    portable=(ROOT/'src/api/reviews/portable.py').read_text(encoding='utf-8')
    combined='\n'.join((cuts,portable))
    assert "feature_call('import_export', 'save_upload_limited')" not in combined
    assert combined.count("feature_call('import_export.uploads', 'save_upload_limited')")==2


def test_v10225_workspace_backups_are_explicit_service_package():
    assert BACKUPS.is_dir() and not (ROOT/'src/workspace/backups.py').exists()
    required={'__init__.py','README.md','destinations.py','authority.py','state.py','publication.py','catch_up.py','inventory.py'}
    assert required <= {p.name for p in BACKUPS.iterdir()}
    boundary=(BACKUPS/'__init__.py').read_text(encoding='utf-8')
    assert 'class BackupWorkspaceMixin(' in boundary
    assert 'def _backup_destinations' not in boundary
    assert 'def backup_destinations_catch_up' not in boundary
    service=(ROOT/'src/workspace/service.py').read_text(encoding='utf-8')
    assert 'from .backups import BackupWorkspaceMixin' in service


def test_v10225_decomposed_owners_are_bounded():
    import_impl=[p for p in IMPORTS.glob('*.py') if p.name!='__init__.py']
    backup_impl=[p for p in BACKUPS.glob('*.py') if p.name!='__init__.py']
    assert max(len(p.read_text(encoding='utf-8').splitlines()) for p in import_impl) < 450
    assert max(len(p.read_text(encoding='utf-8').splitlines()) for p in backup_impl) < 450
