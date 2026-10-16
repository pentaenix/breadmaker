from __future__ import annotations

import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'src'


def test_v10215_dead_storage_facade_stays_deleted_and_unreferenced():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (10,2,15)
    assert not (SRC/'storage'/'core.py').exists()
    for base in (SRC,ROOT/'scripts'):
        for path in base.rglob('*.py'):
            source=path.read_text(encoding='utf-8')
            tree=ast.parse(source,filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node,ast.ImportFrom):
                    assert node.module!='storage.core',path
                elif isinstance(node,ast.Import):
                    assert all(alias.name!='storage.core' for alias in node.names),path


def test_v10215_historical_source_contracts_do_not_read_deleted_storage_core():
    offenders=[]
    for path in (ROOT/'tests').glob('test_*.py'):
        if path.name in {'test_v10214.py','test_v10215.py','test_v1024.py'}:
            continue
        source=path.read_text(encoding='utf-8')
        if "'storage'/'core.py'" in source or "'storage','core.py'" in source or 'storage/core.py' in source:
            offenders.append(path.name)
    assert offenders==[]


def test_v10215_app_is_explicit_composition_not_namespace_bridge():
    source=(SRC/'app.py').read_text(encoding='utf-8')
    assert 'from backend.core import app as _platform_app' in source
    assert '_install_routers(_platform_app)' in source
    assert 'app=_platform_app' in source
    assert 'sys.modules[__name__]' not in source
