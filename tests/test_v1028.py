from __future__ import annotations

import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BACKEND=ROOT/'src'/'backend'


def test_v1028_release_and_core_is_composition_only():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (10,2,8)
    source=(BACKEND/'core.py').read_text(encoding='utf-8')
    assert len(source.splitlines()) <= 80
    tree=ast.parse(source)
    functions=[node.name for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))]
    assert functions==[]
    assert 'http_boundary.install(app)' in source
    assert 'request_pipeline.install(app)' in source
    assert 'app.include_router(project_management.router)' in source
    assert 'app.include_router(runtime_routes.router)' in source


def test_v1028_focused_platform_owners_exist_and_do_not_import_core():
    owners=(
        'local_history.py','lifecycle.py','http_boundary.py','client_leases.py',
        'danger_challenges.py','request_pipeline.py','runtime_routes.py',
    )
    for name in owners:
        source=(BACKEND/name).read_text(encoding='utf-8')
        tree=ast.parse(source,filename=name)
        for node in ast.walk(tree):
            if isinstance(node,ast.ImportFrom):
                assert node.module!='backend.core',name
            elif isinstance(node,ast.Import):
                assert all(alias.name!='backend.core' for alias in node.names),name


def test_v1028_refactor_sensitive_tests_target_new_canonical_owners():
    v780=(ROOT/'tests/test_v780.py').read_text(encoding='utf-8')
    v9612=(ROOT/'tests/test_v9612.py').read_text(encoding='utf-8')
    v775=(ROOT/'tests/test_v775_scheduler_integration.py').read_text(encoding='utf-8')
    v967=(ROOT/'tests/test_v967.py').read_text(encoding='utf-8')
    assert "monkeypatch.setattr(local_history,'_LOCAL_HISTORY_DEBOUNCE_SECONDS'" in v780
    assert 'target=local_history._local_history_worker_loop' in v780
    assert 'danger_challenges._new_danger_challenge' in v9612
    assert 'client_leases._claim_client_lease' in v9612
    assert "monkeypatch.setattr(request_pipeline,'startup'" in v775
    assert "monkeypatch.setattr(request_pipeline,'_request_project_identity'" in v967
    assert "monkeypatch.setattr(request_pipeline,'_request_user_identity'" in v967


def test_v1028_docs_declare_thin_core_boundary():
    architecture=(ROOT/'ARCHITECTURE.md').read_text(encoding='utf-8')
    agents=(ROOT/'AGENTS.md').read_text(encoding='utf-8')
    backend=(BACKEND/'README.md').read_text(encoding='utf-8')
    assert '`backend.core` is now composition-only' in architecture
    assert '`src/backend/core.py` is composition-only' in agents
    assert '`core.py` — thin FastAPI composition only' in backend
