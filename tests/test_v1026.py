from __future__ import annotations

import ast
import importlib
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
API=ROOT/'src'/'api'


def test_v1026_release_and_feature_modules_have_zero_backend_core_imports():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (10,2,6)
    offenders=[]
    for path in API.glob('*.py'):
        tree=ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
        for node in tree.body:
            if isinstance(node,ast.ImportFrom) and node.module=='backend.core':
                offenders.append((path.name,node.lineno,[alias.name for alias in node.names]))
            elif isinstance(node,ast.Import):
                for alias in node.names:
                    if alias.name=='backend.core':offenders.append((path.name,node.lineno,[alias.name]))
    assert offenders==[]


def test_v1026_stateful_platform_services_are_canonical_and_api_neutral():
    modules=(
        'backend.portable_review_runtime',
        'backend.recovery_runtime',
        'backend.history_runtime',
        'backend.project_activation',
    )
    for name in modules:
        module=importlib.import_module(name)
        assert module.__name__==name
        source=Path(module.__file__).read_text(encoding='utf-8')
        assert 'from api.' not in source and 'import api.' not in source


def test_v1026_refactor_sensitive_tests_patch_the_canonical_request_context_owner():
    quota=(ROOT/'tests/test_v9611.py').read_text(encoding='utf-8')
    security=(ROOT/'tests/test_v9613.py').read_text(encoding='utf-8')
    assert 'from backend import request_context' in quota
    assert "monkeypatch.setattr(request_context.runtime.usage,'owner_storage_bytes'" in quota
    assert "monkeypatch.setattr(request_context.runtime.usage,'owner_project_count'" in quota
    assert 'request_context._enforce_hosted_new_project_quota' in quota
    assert 'from backend import request_context' in security
    assert "monkeypatch.setattr(request_context,'runtime',hosted)" in security
    assert 'request_context._public_project_record(row)' in security
    assert 'request_context._require_hosted_project_management()' in security


def test_v1026_documentation_declares_the_zero_core_dependency_boundary():
    agents=(ROOT/'AGENTS.md').read_text(encoding='utf-8')
    architecture=(ROOT/'ARCHITECTURE.md').read_text(encoding='utf-8')
    api_readme=(ROOT/'src/api/README.md').read_text(encoding='utf-8')
    backend_readme=(ROOT/'src/backend/README.md').read_text(encoding='utf-8')
    assert 'must not import `backend.core`' in agents
    assert 'zero imports from `backend.core`' in architecture
    assert 'must not import `backend.core`' in api_readme
    assert 'must not import `backend.core`' in backend_readme
