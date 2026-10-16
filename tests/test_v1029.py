from __future__ import annotations

import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'src'
BACKEND=SRC/'backend'


def test_v1029_release_and_local_history_observer_registration_are_explicit():
    assert tuple(map(int,(ROOT/'VERSION').read_text(encoding='utf-8').strip().split('.'))) >= (10,2,9)
    source=(BACKEND/'local_history.py').read_text(encoding='utf-8')
    assert 'def _canonical_commit_history_observer(event:dict)' in source
    assert 'storage_mutations.register_canonical_commit_listener(_canonical_commit_history_observer)' in source
    assert source.index('def _canonical_commit_history_observer') < source.index('storage_mutations.register_canonical_commit_listener(_canonical_commit_history_observer)')


def test_v1029_outer_server_reads_hosted_and_bootstrap_dependencies_from_canonical_owners():
    source=(SRC/'server.py').read_text(encoding='utf-8')
    tree=ast.parse(source)
    assert 'from backend import environment, request_pipeline' in source
    assert 'from security import hosted_guardrails' in source
    assert 'environment._HOSTED_LIMITS' in source
    assert 'hosted_guardrails.body_limit_for_path' in source
    assert 'request_pipeline._runtime_bootstrap_payload()' in source
    assert "getattr(core,'_HOSTED_LIMITS'" not in source
    assert 'core.hosted_guardrails' not in source
    assert "getattr(core,'_runtime_bootstrap_payload'" not in source
    # The composition module is still the single delegated FastAPI app.
    assert 'await core.app(scope,receive,send)' in source


def test_v1029_refactor_sensitive_security_tests_target_canonical_runtime_owners():
    v9611=(ROOT/'tests/test_v9611.py').read_text(encoding='utf-8')
    v963=(ROOT/'tests/test_v963.py').read_text(encoding='utf-8')
    v969=(ROOT/'tests/test_v969.py').read_text(encoding='utf-8')
    assert "monkeypatch.setattr(server.environment,'_HOSTED_LIMITS',limits)" in v9611
    assert "server.core,'_HOSTED_LIMITS'" not in v9611
    assert 'from backend.environment import runtime' in v963
    assert 'app.runtime' not in v963
    assert 'request_pipeline._runtime_bootstrap_payload()' in v969
    assert "getattr(core,'_runtime_bootstrap_payload',None)" not in v969


def test_v1029_thin_core_boundary_stays_thin():
    source=(BACKEND/'core.py').read_text(encoding='utf-8')
    assert len(source.splitlines()) <= 80
    assert '_HOSTED_LIMITS' not in source
    assert '_runtime_bootstrap_payload' not in source
    assert 'runtime =' not in source
