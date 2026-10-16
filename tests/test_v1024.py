from __future__ import annotations

import ast
import importlib
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'src'


def _version_tuple(value:str) -> tuple[int,int,int]:
    parts=value.strip().split('.')
    assert len(parts)==3 and all(part.isdigit() for part in parts)
    return tuple(int(part) for part in parts)


def test_v1024_release_identity_and_moved_modules_have_canonical_import_paths():
    assert _version_tuple((ROOT/'VERSION').read_text(encoding='utf-8')) >= (10,2,4)
    forbidden={'sessions','content_security','hosted_guardrails','breadreview'}
    offenders=[]
    for base in (SRC,ROOT/'tests'):
        for path in base.rglob('*.py'):
            tree=ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node,ast.Import):
                    for alias in node.names:
                        if alias.name=='storage.core' or alias.name in forbidden:
                            offenders.append((path.relative_to(ROOT).as_posix(),node.lineno,alias.name))
                elif isinstance(node,ast.ImportFrom):
                    module=str(node.module or '')
                    if module in forbidden or module=='storage.core' or (module=='storage' and any(alias.name=='core' for alias in node.names)):
                        offenders.append((path.relative_to(ROOT).as_posix(),node.lineno,module or 'storage.core'))
    assert offenders==[]


def test_v1024_storage_package_is_lightweight_and_owners_are_explicit():
    package=importlib.import_module('storage')
    schema=importlib.import_module('storage.schema')
    mutations=importlib.import_module('storage.mutations')
    history=importlib.import_module('storage.history')
    assert not (SRC/'storage'/'core.py').exists()
    assert not hasattr(package,'tx')
    assert callable(mutations.tx)
    assert callable(history.local_revision_store_for)
    assert schema.SCHEMA_VERSION==package.SCHEMA_VERSION


def test_v1024_relocated_modules_import_from_their_explicit_owners():
    modules=(
        'auth.sessions',
        'security.content_security',
        'security.hosted_guardrails',
        'formats.breadreview',
        'storage.context',
        'storage.database',
        'storage.mutations',
        'storage.history',
    )
    for name in modules:
        assert importlib.import_module(name).__name__==name
