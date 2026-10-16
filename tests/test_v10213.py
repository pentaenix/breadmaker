from __future__ import annotations

import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'src'
STORAGE=SRC/'storage'


def test_v10213_release_identity_and_storage_core_compatibility_boundary_is_gone():
    assert tuple(map(int,(ROOT/'VERSION').read_text().strip().split('.'))) >= (10,2,13)
    assert not (STORAGE/'core.py').exists()


def test_v10213_code_does_not_import_storage_core():
    offenders=[]
    for base in (SRC,ROOT/'tests',ROOT/'scripts'):
        for path in base.rglob('*.py'):
            tree=ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node,ast.ImportFrom) and node.module in {'storage','storage.core'}:
                    if node.module=='storage.core' or any(alias.name=='core' for alias in node.names):
                        offenders.append((str(path.relative_to(ROOT)),node.lineno))
                if isinstance(node,ast.Import) and any(alias.name=='storage.core' for alias in node.names):
                    offenders.append((str(path.relative_to(ROOT)),node.lineno))
    assert offenders==[]


def test_v10213_storage_package_has_explicit_owners_and_boundary_only_init():
    expected={
        'context.py','coordination.py','schema.py','schema_contracts.py','values.py',
        'database.py','mutations.py','history.py','undo.py','assets.py',
        'invariants.py','integrity.py','recovery.py','README.md',
    }
    assert expected <= {p.name for p in STORAGE.iterdir()}
    assert 'core.py' not in {p.name for p in STORAGE.iterdir()}
    init=(STORAGE/'__init__.py').read_text(encoding='utf-8')
    tree=ast.parse(init)
    assert not [n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
    assert 'Import the owner that defines the behavior you need.' in init


def test_v10213_cross_storage_runtime_edges_use_canonical_module_owners():
    for name in ('database','mutations','history','undo','assets','integrity','recovery'):
        source=(STORAGE/f'{name}.py').read_text(encoding='utf-8')
        assert 'from storage.core' not in source
    assert 'storage_database.connect()' in (STORAGE/'mutations.py').read_text(encoding='utf-8')
    assert 'storage_context.current_project_root()' in (STORAGE/'history.py').read_text(encoding='utf-8')
    assert 'storage_coordination._LOCK' in (STORAGE/'undo.py').read_text(encoding='utf-8')
    assert 'storage_database.connect()' in (STORAGE/'assets.py').read_text(encoding='utf-8')
